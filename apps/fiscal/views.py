import json
import io
import csv
import hashlib
import logging
import re
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.financeiro.models.fiscal import (
    DocumentoFiscal,
    InutilizacaoNumeracao,
    LogIntegracaoFiscal,
)
from apps.financeiro.constants.enums import StatusDocumentoFiscal
from apps.fiscal.integrations.dfe_client import avaliar_prontidao_dfe
from apps.fiscal.integrations.focusnfe import FocusNFeClient
from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig
from apps.fiscal.integrations.focusnfe.exceptions import FocusNFeError
from apps.fiscal.models import ManifestoFiscalConfig, ManifestoFiscalDocumento
from apps.fiscal.services.certificado_a1 import validar_certificado_a1_para_config
from apps.fiscal.services.focusnfe_service import (
    RESOURCE_POR_TIPO,
    FocusNFeService,
    gerar_ref,
    parse_ref,
)
from apps.fiscal.services.focusnfe_backup_service import (
    FocusNFeBackupService,
    classificar_xml_fiscal,
)
from apps.fiscal.services.manifesto_service import ManifestoFiscalService

logger = logging.getLogger(__name__)


def _focus_service_documento(documento):
    from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig

    filial = documento.filial
    token = (filial.focusnfe_token or "").strip()
    if not token:
        raise DomainError("Configure o token de emissao Focus da filial.")
    client = FocusNFeClient(
        config=FocusNFeConfig.from_env(
            token=token,
            ambiente=filial.focusnfe_ambiente,
        ),
    )
    return FocusNFeService(client=client)


def _nome_xml(documento, sufixo):
    chave = documento.chave or f"{documento.tipo_documento}-{documento.numero}-{documento.serie}"
    chave = re.sub(r"[^0-9A-Za-z_-]+", "-", chave)
    return f"{chave}-{sufixo}.xml"


def _obter_xml_documento(documento, tipo):
    if tipo == "autorizado":
        xml = documento.xml_assinado or ""
        if xml.lstrip().startswith("<"):
            return xml
        service = _focus_service_documento(documento)
        xml = service.garantir_xml_autorizado(documento)
        return xml
    if tipo == "cancelamento":
        if documento.status != StatusDocumentoFiscal.CANCELADA:
            raise Http404("Este documento nao esta cancelado.")
        xml = documento.xml_cancelamento or ""
        if xml.lstrip().startswith("<"):
            return xml
        service = _focus_service_documento(documento)
        return service.garantir_xml_cancelamento(documento)
    raise Http404("Tipo de XML invalido.")


def _obter_xml_documento_arquivado(documento, tipo):
    if tipo == "autorizado":
        xml = documento.xml_assinado or ""
    elif tipo == "cancelamento":
        xml = documento.xml_cancelamento or ""
    else:
        return ""
    return xml if xml.lstrip().startswith("<") else ""


def _xml_texto(conteudo):
    if isinstance(conteudo, str):
        return conteudo
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return conteudo.decode(encoding)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("utf-8", errors="replace")


def _xml_e_cancelamento(xml):
    inicio = (xml or "")[:5000].lower()
    marcadores = (
        "<proceventonfe",
        "<proceventocte",
        "<proceventomdfe",
        "<retevento",
        "<tpevento>110111",
    )
    return any(marcador in inicio for marcador in marcadores)


def _fontes_xml_retorno(valor, chave_pai=""):
    fontes = []
    if isinstance(valor, dict):
        for chave, item in valor.items():
            fontes.extend(_fontes_xml_retorno(item, str(chave).lower()))
    elif isinstance(valor, list):
        for item in valor:
            fontes.extend(_fontes_xml_retorno(item, chave_pai))
    elif isinstance(valor, str):
        texto = valor.strip()
        if texto.startswith("<"):
            fontes.append(texto)
        elif "xml" in chave_pai and (
            texto.startswith(("http://", "https://", "/"))
            or ".xml" in texto.lower()
        ):
            fontes.append(texto)
    return fontes


def _fontes_xml_por_documento(documentos):
    fontes = defaultdict(lambda: {"autorizado": [], "cancelamento": []})
    ids = [documento.pk for documento in documentos]
    logs = (
        LogIntegracaoFiscal.objects
        .filter(documento_fiscal_id__in=ids)
        .exclude(response_json="")
        .order_by("-created_at")
        .values("documento_fiscal_id", "acao", "response_json")
    )
    for log in logs:
        try:
            retorno = json.loads(log["response_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        status = str(
            retorno.get("status", "") if isinstance(retorno, dict) else ""
        ).lower()
        tipo = (
            "cancelamento"
            if log["acao"] == "cancelar" or status in {"cancelado", "cancelada"}
            else "autorizado"
        )
        for fonte in _fontes_xml_retorno(retorno):
            if fonte not in fontes[log["documento_fiscal_id"]][tipo]:
                fontes[log["documento_fiscal_id"]][tipo].append(fonte)
    return fontes


def _baixar_xml_exportacao(job):
    config = FocusNFeConfig(
        token=job["token"],
        ambiente=job["ambiente"],
        timeout=5,
        max_retries=0,
    )
    client = FocusNFeClient(config=config)
    fontes = list(job["fontes"][:2])
    recurso = RESOURCE_POR_TIPO.get(job["tipo_documento"])
    if recurso:
        fontes.append(f"/v2/{recurso}/{job['ref']}.xml")

    for fonte in fontes:
        try:
            conteudo = (
                fonte
                if fonte.lstrip().startswith("<")
                else client.http.get(fonte, binary=True)
            )
            xml = _xml_texto(conteudo or "")
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "Falha ao recuperar XML fiscal %s/%s pela fonte %s: %s",
                job["documento_id"],
                job["arquivo"],
                fonte,
                exc,
            )
            continue
        if not xml.lstrip().startswith("<"):
            continue
        cancelamento = _xml_e_cancelamento(xml)
        if (
            job["arquivo"] == "cancelamento" and cancelamento
        ) or (
            job["arquivo"] == "autorizado" and not cancelamento
        ):
            return job["documento_id"], job["arquivo"], xml
    return job["documento_id"], job["arquivo"], ""


def _chave_numerica(documento):
    chave = re.sub(r"\D", "", documento.chave or "")
    return chave if len(chave) == 44 else ""


def _pasta_tipo_documento(tipo_documento):
    return {
        "nfe": "NF-e",
        "nfce": "NFC-e",
    }.get((tipo_documento or "").lower(), "Outros")


def _recuperar_xmls_backup_focus(documentos):
    pendentes_por_filial = defaultdict(list)
    for documento in documentos:
        chave = _chave_numerica(documento)
        if not chave:
            continue
        tipos = ["autorizado"]
        if documento.status == StatusDocumentoFiscal.CANCELADA:
            tipos.append("cancelamento")
        tipos_pendentes = [
            tipo
            for tipo in tipos
            if not _obter_xml_documento_arquivado(documento, tipo)
        ]
        if tipos_pendentes:
            pendentes_por_filial[documento.filial_id].append(
                (documento, chave, tipos_pendentes)
            )

    recuperados = []
    for itens in pendentes_por_filial.values():
        filial = itens[0][0].filial
        token = (filial.focusnfe_token or "").strip()
        if not token or filial.focusnfe_ambiente != 1:
            continue
        meses = {
            documento.data_emissao.strftime("%Y%m")
            for documento, _, _ in itens
            if documento.data_emissao
        }
        pendentes_por_chave = {
            chave: {
                "documento": documento,
                "tipos": set(tipos),
            }
            for documento, chave, tipos in itens
        }
        try:
            client = FocusNFeClient(
                config=FocusNFeConfig(
                    token=token,
                    ambiente=filial.focusnfe_ambiente,
                    timeout=15,
                    max_retries=1,
                )
            )
            service = FocusNFeBackupService(client)
            backups = service.selecionar(filial.cnpj, meses)
            for xml_backup in service.iter_xmls(backups):
                xml = xml_backup.conteudo
                if not xml.lstrip().startswith("<"):
                    continue
                chaves_xml = set(re.findall(r"(?<!\d)\d{44}(?!\d)", xml_backup.nome))
                chaves_xml.update(re.findall(r"(?<!\d)\d{44}(?!\d)", xml))
                for chave in chaves_xml.intersection(pendentes_por_chave):
                    pendente = pendentes_por_chave[chave]
                    tipo = (
                        "cancelamento"
                        if _xml_e_cancelamento(xml)
                        else "autorizado"
                    )
                    if tipo not in pendente["tipos"]:
                        continue
                    recuperados.append(
                        (pendente["documento"].pk, tipo, xml)
                    )
                    pendente["tipos"].discard(tipo)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "Backup Focus indisponivel para %s; usando fallback individual: %s",
                filial.cnpj,
                exc,
            )

    documentos_por_id = {documento.pk: documento for documento in documentos}
    for documento_id, tipo, xml in recuperados:
        campo = "xml_assinado" if tipo == "autorizado" else "xml_cancelamento"
        DocumentoFiscal.objects.filter(pk=documento_id).update(**{campo: xml})
        setattr(documentos_por_id[documento_id], campo, xml)


def _recuperar_xmls_exportacao(
    documentos,
    *,
    usar_backup_focus=True,
    usar_consulta_individual=True,
):
    if usar_backup_focus:
        _recuperar_xmls_backup_focus(documentos)
    if not usar_consulta_individual:
        return
    fontes_logs = _fontes_xml_por_documento(documentos)
    jobs = []
    documentos_por_id = {documento.pk: documento for documento in documentos}
    for documento in documentos:
        tipos = ["autorizado"]
        if documento.status == StatusDocumentoFiscal.CANCELADA:
            tipos.append("cancelamento")
        for tipo in tipos:
            if _obter_xml_documento_arquivado(documento, tipo):
                continue
            fontes = list(fontes_logs[documento.pk][tipo])
            xml_retorno = (documento.xml_retorno or "").strip()
            tipo_retorno = (
                "cancelamento"
                if documento.status == StatusDocumentoFiscal.CANCELADA
                else "autorizado"
            )
            if (
                tipo == tipo_retorno
                and xml_retorno
                and xml_retorno not in fontes
            ):
                fontes.insert(0, xml_retorno)
            token = (documento.filial.focusnfe_token or "").strip()
            if not token:
                continue
            jobs.append({
                "documento_id": documento.pk,
                "arquivo": tipo,
                "tipo_documento": documento.tipo_documento,
                "ref": gerar_ref(documento),
                "token": token,
                "ambiente": documento.filial.focusnfe_ambiente,
                "fontes": fontes,
            })

    if not jobs:
        return

    recuperados = []
    workers = min(10, len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futuros = [executor.submit(_baixar_xml_exportacao, job) for job in jobs]
        for futuro in as_completed(futuros):
            try:
                resultado = futuro.result()
            except Exception:  # noqa: BLE001
                logger.exception("Falha inesperada ao recuperar XML para exportacao.")
                continue
            if resultado[2]:
                recuperados.append(resultado)

    for documento_id, tipo, xml in recuperados:
        campo = "xml_assinado" if tipo == "autorizado" else "xml_cancelamento"
        DocumentoFiscal.objects.filter(pk=documento_id).update(**{campo: xml})
        setattr(documentos_por_id[documento_id], campo, xml)


def _filtro_data(queryset, campo, data_inicial, data_final):
    if data_inicial:
        queryset = queryset.filter(**{f"{campo}__date__gte": data_inicial})
    if data_final:
        queryset = queryset.filter(**{f"{campo}__date__lte": data_final})
    return queryset


def _filtro_origem_documentos(queryset, origem):
    if origem == "vendas":
        return queryset.filter(origem_tipo="venda_pdv")
    if origem == "transferencias":
        return queryset.filter(
            origem_tipo__in=["transferencia_estoque", "mdfe"],
        )
    if origem == "outras":
        return queryset.exclude(
            origem_tipo__in=["venda_pdv", "transferencia_estoque", "mdfe"],
        )
    return queryset


def _documentos_fiscais_operacionais(filial):
    documentos_concluidos = DocumentoFiscal.objects.for_filial(filial).filter(
        origem_tipo=OuterRef("origem_tipo"),
        origem_id=OuterRef("origem_id"),
        status__in=[
            StatusDocumentoFiscal.AUTORIZADA,
            StatusDocumentoFiscal.CANCELADA,
        ],
    )
    return (
        DocumentoFiscal.objects.for_filial(filial)
        .annotate(_tem_documento_concluido=Exists(documentos_concluidos))
        .exclude(
            Q(status__in=[
                StatusDocumentoFiscal.REJEITADA,
                StatusDocumentoFiscal.DENEGADA,
            ])
            & Q(origem_id__gt=0)
            & Q(_tem_documento_concluido=True)
        )
    )


class ManifestoFiscalListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    template_name = 'fiscal/manifesto/list.html'

    def get(self, request):
        aba = request.GET.get('aba', 'saidas')
        origem_export = (request.GET.get('origem_export') or '').strip()
        documentos = ManifestoFiscalDocumento.objects.for_filial(request.filial_ativa).order_by('-created_at')
        page_obj = Paginator(documentos, 30).get_page(request.GET.get('page'))
        kpis = {
            'pendentes': documentos.filter(
                status_manifestacao=ManifestoFiscalDocumento.StatusManifestacao.NAO_MANIFESTADA,
            ).count(),
            'xml': documentos.filter(
                status_download_xml__in=[
                    ManifestoFiscalDocumento.StatusDownload.XML_DISPONIVEL,
                    ManifestoFiscalDocumento.StatusDownload.XML_BAIXADO,
                ],
            ).count(),
            'importadas': documentos.filter(
                status_download_xml=ManifestoFiscalDocumento.StatusDownload.IMPORTADA,
            ).count(),
        }
        saidas = (
            _documentos_fiscais_operacionais(request.filial_ativa)
            .select_related('usuario')
            .order_by('-created_at')
        )
        saidas = _filtro_origem_documentos(saidas, origem_export)
        status_saida = (request.GET.get('status') or '').strip()
        if status_saida:
            saidas = saidas.filter(status=status_saida)
        saidas_page_obj = Paginator(saidas, 30).get_page(
            request.GET.get('page_saida'),
        )
        for documento in saidas_page_obj.object_list:
            documento.origem_url = ''
            documento.mdfe_url = ''
            if documento.origem_tipo == 'transferencia_estoque':
                documento.origem_url = reverse('estoque:transferencia-lojas')
            try:
                mdfe = documento.mdfe_logistico
            except Exception:  # noqa: BLE001
                mdfe = None
            if mdfe:
                documento.mdfe_url = reverse(
                    'logistica:mdfe-detail',
                    kwargs={'pk': mdfe.pk},
                )

        saidas_base = _filtro_origem_documentos(
            _documentos_fiscais_operacionais(request.filial_ativa),
            origem_export,
        )
        kpis_saida = {
            'total': saidas_base.count(),
            'processando': saidas_base.filter(
                status__in=[
                    StatusDocumentoFiscal.PENDENTE,
                    StatusDocumentoFiscal.PROCESSANDO,
                ],
            ).count(),
            'autorizadas': saidas_base.filter(
                status=StatusDocumentoFiscal.AUTORIZADA,
            ).count(),
            'erros': saidas_base.filter(
                status__in=[
                    StatusDocumentoFiscal.REJEITADA,
                    StatusDocumentoFiscal.DENEGADA,
                ],
            ).count(),
            'inutilizadas': saidas_base.filter(
                status=StatusDocumentoFiscal.INUTILIZADA,
            ).count(),
        }
        inutilizacoes = (
            InutilizacaoNumeracao.objects
            .filter(filial=request.filial_ativa)
            .select_related('usuario')
            .order_by('-created_at')[:20]
        )
        inutilizacoes_legadas = (
            saidas_base
            .filter(
                status=StatusDocumentoFiscal.INUTILIZADA,
                origem_id=0,
            )
            .select_related('usuario')
            .order_by('-data_emissao')[:20]
        )
        config = ManifestoFiscalConfig.objects.for_filial(request.filial_ativa).filter(ativo=True).first()
        return render(request, self.template_name, {
            'aba': aba,
            'documentos': page_obj.object_list,
            'page_obj': page_obj,
            'kpis': kpis,
            'documentos_saida': saidas_page_obj.object_list,
            'saidas_page_obj': saidas_page_obj,
            'kpis_saida': kpis_saida,
            'status_saida': status_saida,
            'inutilizacoes': inutilizacoes,
            'inutilizacoes_legadas': inutilizacoes_legadas,
            'origem_export': origem_export,
            'config': config,
        })

    def post(self, request):
        try:
            resultado = ManifestoFiscalService.sincronizar_documentos(request.filial_ativa, request.user)
        except DomainError as exc:
            messages.error(request, str(exc))
            return redirect('fiscal:manifesto-list')

        if resultado.total_documentos:
            messages.success(
                request,
                (
                    f'Consulta DF-e concluida: {resultado.criados} novo(s), '
                    f'{resultado.atualizados} atualizado(s).'
                ),
            )
        else:
            messages.info(
                request,
                resultado.mensagem or 'Consulta DF-e executada em modo seguro; nenhum documento novo.',
            )
        return redirect('fiscal:manifesto-list')


class DocumentoFiscalSaidaConsultarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'ver'

    def post(self, request, pk):
        from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig
        from apps.logistica.services.mdfe_focusnfe import (
            processar_nfe_transferencia_autorizada,
            sincronizar_mdfe_por_documento,
        )

        documento = get_object_or_404(
            DocumentoFiscal.objects.for_filial(request.filial_ativa),
            pk=pk,
        )
        filial = documento.filial
        token = (filial.focusnfe_token or '').strip()
        if not token:
            messages.error(request, 'Configure o token de emissão Focus da filial.')
            return redirect(f"{reverse('fiscal:manifesto-list')}?aba=saidas")

        try:
            client = FocusNFeClient(
                config=FocusNFeConfig.from_env(
                    token=token,
                    ambiente=filial.focusnfe_ambiente,
                ),
            )
            FocusNFeService(client=client).consultar(documento)
            documento.refresh_from_db()
            sincronizar_mdfe_por_documento(documento)
            processar_nfe_transferencia_autorizada(documento)
        except Exception as exc:  # noqa: BLE001
            messages.error(request, f'Falha ao consultar documento na SEFAZ: {exc}')
        else:
            messages.success(
                request,
                f'{documento.get_tipo_documento_display()} nº {documento.numero}: '
                f'{documento.get_status_display()}.',
            )
        return redirect(f"{reverse('fiscal:manifesto-list')}?aba=saidas")


class DocumentoFiscalSaidaDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = "compras"
    permissao_acao = "ver"
    template_name = "fiscal/manifesto/saida_detail.html"

    def get(self, request, pk):
        documento = get_object_or_404(
            DocumentoFiscal.objects.for_filial(request.filial_ativa).select_related(
                "usuario", "filial"
            ),
            pk=pk,
        )
        logs = list(
            LogIntegracaoFiscal.objects.filter(documento_fiscal=documento)
            .select_related("usuario")
            .order_by("created_at")
        )
        for log in logs:
            try:
                request_data = json.loads(log.request_json or "{}")
            except (TypeError, ValueError):
                request_data = {}
            log.justificativa = request_data.get("justificativa", "")
        return render(
            request,
            self.template_name,
            {"documento": documento, "logs": logs},
        )


class DocumentoFiscalXMLView(PermissaoRequiredMixin, View):
    permissao_modulo = "compras"
    permissao_acao = "ver"

    def get(self, request, pk, tipo):
        documento = get_object_or_404(
            DocumentoFiscal.objects.for_filial(request.filial_ativa).select_related("filial"),
            pk=pk,
        )
        try:
            xml = _obter_xml_documento(documento, tipo)
        except DomainError as exc:
            return HttpResponse(str(exc), status=422, content_type="text/plain; charset=utf-8")
        if not xml or not xml.lstrip().startswith("<"):
            return HttpResponse(
                "O XML ainda nao esta disponivel no provedor fiscal.",
                status=404,
                content_type="text/plain; charset=utf-8",
            )
        response = HttpResponse(xml, content_type="application/xml; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="{_nome_xml(documento, tipo)}"'
        )
        return response


class DocumentoFiscalExportarXMLView(PermissaoRequiredMixin, View):
    permissao_modulo = "compras"
    permissao_acao = "ver"

    def get(self, request):
        situacao = (request.GET.get("situacao") or "todas").strip()
        origem = (request.GET.get("origem") or "").strip()
        tipo_documento = (request.GET.get("tipo_documento") or "").strip()
        fonte = (request.GET.get("fonte") or "automatica").strip()
        if fonte not in {"automatica", "erp"}:
            fonte = "automatica"
        data_inicial = parse_date((request.GET.get("data_ini") or "").strip())
        data_final = parse_date((request.GET.get("data_fim") or "").strip())
        if data_inicial and data_final and data_inicial > data_final:
            return HttpResponseBadRequest(
                "A data inicial nao pode ser posterior a data final.",
            )

        status_legado = (request.GET.get("status") or "").strip()
        if status_legado == StatusDocumentoFiscal.AUTORIZADA:
            situacao = "emitidas"
        elif status_legado == StatusDocumentoFiscal.CANCELADA:
            situacao = "canceladas"

        documentos = (
            DocumentoFiscal.objects.for_filial(request.filial_ativa)
            .select_related("filial")
            .filter(
                status__in=[
                    StatusDocumentoFiscal.AUTORIZADA,
                    StatusDocumentoFiscal.CANCELADA,
                ]
            )
            .order_by("data_emissao")
        )
        if situacao == "emitidas":
            documentos = documentos.filter(status=StatusDocumentoFiscal.AUTORIZADA)
        elif situacao == "canceladas":
            documentos = documentos.filter(status=StatusDocumentoFiscal.CANCELADA)
        elif situacao == "inutilizadas":
            documentos = documentos.none()
        if tipo_documento:
            documentos = documentos.filter(tipo_documento=tipo_documento)
        documentos = _filtro_origem_documentos(documentos, origem)
        documentos = _filtro_data(
            documentos,
            "data_emissao",
            data_inicial,
            data_final,
        )

        inutilizacoes = (
            InutilizacaoNumeracao.objects
            .filter(filial=request.filial_ativa)
            .select_related("usuario")
            .order_by("created_at")
        )
        inutilizacoes = _filtro_data(
            inutilizacoes,
            "created_at",
            data_inicial,
            data_final,
        )
        if tipo_documento:
            inutilizacoes = inutilizacoes.filter(tipo_documento=tipo_documento)
        if situacao not in {"todas", "inutilizadas"} or origem:
            inutilizacoes = inutilizacoes.none()

        documentos = list(documentos)
        _recuperar_xmls_exportacao(
            documentos,
            usar_backup_focus=fonte == "automatica",
            usar_consulta_individual=fonte == "automatica",
        )

        buffer = io.BytesIO()
        adicionados = 0
        documentos_exportados = 0
        faixas_exportadas = 0
        xmls_pendentes = []
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as arquivo:
            for documento in documentos:
                documentos_exportados += 1
                tipos = ["autorizado"]
                if documento.status == StatusDocumentoFiscal.CANCELADA:
                    tipos.append("cancelamento")
                for tipo in tipos:
                    xml = _obter_xml_documento_arquivado(documento, tipo)
                    if xml and xml.lstrip().startswith("<"):
                        pasta = (
                            "canceladas"
                            if documento.status == StatusDocumentoFiscal.CANCELADA
                            else "emitidas"
                        )
                        arquivo.writestr(
                            (
                                f"{pasta}/"
                                f"{_pasta_tipo_documento(documento.tipo_documento)}/"
                                f"{_nome_xml(documento, tipo)}"
                            ),
                            xml,
                        )
                        adicionados += 1
                    else:
                        xmls_pendentes.append({
                            "id": documento.pk,
                            "tipo_documento": documento.tipo_documento,
                            "numero": documento.numero,
                            "serie": documento.serie,
                            "chave": documento.chave or "",
                            "arquivo": tipo,
                        })

            csv_buffer = io.StringIO(newline="")
            writer = csv.writer(csv_buffer, delimiter=";")
            writer.writerow([
                "tipo",
                "serie",
                "numero_inicial",
                "numero_final",
                "data",
                "protocolo",
                "status",
                "justificativa",
                "usuario",
            ])
            faixas_cobertas = []
            for inutilizacao in inutilizacoes.iterator():
                faixas_exportadas += 1
                faixas_cobertas.append((
                    inutilizacao.tipo_documento,
                    inutilizacao.serie,
                    inutilizacao.numero_inicial,
                    inutilizacao.numero_final,
                ))
                writer.writerow([
                    inutilizacao.tipo_documento.upper(),
                    inutilizacao.serie,
                    inutilizacao.numero_inicial,
                    inutilizacao.numero_final,
                    inutilizacao.data_inutilizacao or inutilizacao.created_at,
                    inutilizacao.protocolo,
                    inutilizacao.status,
                    inutilizacao.justificativa,
                    str(inutilizacao.usuario),
                ])
                xml = inutilizacao.xml_retorno or ""
                if xml.lstrip().startswith("<"):
                    nome = (
                        f"inutilizadas/"
                        f"{_pasta_tipo_documento(inutilizacao.tipo_documento)}/"
                        f"{inutilizacao.tipo_documento}-"
                        f"serie-{inutilizacao.serie}-"
                        f"{inutilizacao.numero_inicial}-"
                        f"{inutilizacao.numero_final}.xml"
                    )
                    arquivo.writestr(nome, xml)
                    adicionados += 1

            inutilizados_legados = DocumentoFiscal.objects.for_filial(
                request.filial_ativa,
            ).filter(status=StatusDocumentoFiscal.INUTILIZADA)
            inutilizados_legados = _filtro_data(
                inutilizados_legados,
                "data_emissao",
                data_inicial,
                data_final,
            )
            if tipo_documento:
                inutilizados_legados = inutilizados_legados.filter(
                    tipo_documento=tipo_documento,
                )
            if situacao not in {"todas", "inutilizadas"} or origem:
                inutilizados_legados = inutilizados_legados.none()
            for documento in inutilizados_legados.iterator():
                if any(
                    tipo == documento.tipo_documento
                    and serie == documento.serie
                    and inicio <= documento.numero <= fim
                    for tipo, serie, inicio, fim in faixas_cobertas
                ):
                    continue
                faixas_exportadas += 1
                writer.writerow([
                    documento.tipo_documento.upper(),
                    documento.serie,
                    documento.numero,
                    documento.numero,
                    documento.data_emissao,
                    documento.protocolo,
                    documento.status,
                    documento.mensagem_sefaz or "Registro legado",
                    str(documento.usuario),
                ])
                xml = documento.xml_retorno or ""
                if xml.lstrip().startswith("<"):
                    arquivo.writestr(
                        (
                            f"inutilizadas/"
                            f"{_pasta_tipo_documento(documento.tipo_documento)}/"
                            f"{documento.tipo_documento}-"
                            f"serie-{documento.serie}-{documento.numero}.xml"
                        ),
                        xml,
                    )
                    adicionados += 1
            if faixas_exportadas:
                arquivo.writestr(
                    "inutilizadas/faixas-inutilizadas.csv",
                    "\ufeff" + csv_buffer.getvalue(),
                )

            if xmls_pendentes:
                pendentes_buffer = io.StringIO(newline="")
                pendentes_writer = csv.DictWriter(
                    pendentes_buffer,
                    fieldnames=[
                        "id",
                        "tipo_documento",
                        "numero",
                        "serie",
                        "chave",
                        "arquivo",
                    ],
                    delimiter=";",
                )
                pendentes_writer.writeheader()
                pendentes_writer.writerows(xmls_pendentes)
                arquivo.writestr(
                    "xmls-pendentes.csv",
                    "\ufeff" + pendentes_buffer.getvalue(),
                )

            arquivo.writestr(
                "resumo-exportacao.txt",
                (
                    "Exportacao fiscal do ERP\n"
                    f"Documentos selecionados: {documentos_exportados}\n"
                    f"Faixas inutilizadas: {faixas_exportadas}\n"
                    f"Arquivos XML incluidos: {adicionados}\n"
                    f"Arquivos XML pendentes: {len(xmls_pendentes)}\n"
                    f"Situacao: {situacao}\n"
                    f"Tipo: {tipo_documento or 'todos'}\n"
                    f"Origem: {origem or 'todas'}\n"
                    f"Fonte: {fonte}\n"
                    f"Periodo: {data_inicial or 'inicio'} a {data_final or 'hoje'}\n"
                ),
            )
            if not adicionados:
                arquivo.writestr(
                    "LEIA-ME.txt",
                    (
                        "Nenhum XML oficial estava disponivel para os filtros "
                        "selecionados. Consulte o resumo, o arquivo "
                        "xmls-pendentes.csv e, quando aplicavel, o relatorio "
                        "de faixas inutilizadas."
                    ),
                )
        buffer.seek(0)
        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        periodo = f"{data_inicial or 'inicio'}-{data_final or 'hoje'}"
        response["Content-Disposition"] = (
            f'attachment; filename="documentos-fiscais-{periodo}.zip"'
        )
        return response


class DocumentoFiscalBackupFocusView(PermissaoRequiredMixin, View):
    permissao_modulo = "compras"
    permissao_acao = "ver"

    def get(self, request):
        data_inicial = parse_date((request.GET.get("data_ini") or "").strip())
        data_final = parse_date((request.GET.get("data_fim") or "").strip())
        if data_inicial and data_final and data_inicial > data_final:
            return HttpResponseBadRequest(
                "A data inicial nao pode ser posterior a data final."
            )

        filial = request.filial_ativa
        token = (filial.focusnfe_token or "").strip()
        if not token:
            return HttpResponse(
                "Configure o token de emissao Focus da filial.",
                status=422,
                content_type="text/plain; charset=utf-8",
            )
        if filial.focusnfe_ambiente != 1:
            return HttpResponse(
                "Os backups mensais da Focus estao disponiveis somente em producao.",
                status=422,
                content_type="text/plain; charset=utf-8",
            )

        try:
            client = FocusNFeClient(
                config=FocusNFeConfig(
                    token=token,
                    ambiente=filial.focusnfe_ambiente,
                    timeout=30,
                    max_retries=1,
                )
            )
            service = FocusNFeBackupService(client)
            backups = service.listar(filial.cnpj)
            mes_inicial = data_inicial.strftime("%Y%m") if data_inicial else ""
            mes_final = data_final.strftime("%Y%m") if data_final else ""
            if not mes_inicial and not mes_final and backups:
                backups = [backups[-1]]
            else:
                backups = [
                    backup
                    for backup in backups
                    if (not mes_inicial or backup.mes >= mes_inicial)
                    and (not mes_final or backup.mes <= mes_final)
                ]
            if not backups:
                return HttpResponse(
                    "A Focus ainda nao disponibilizou backups para o periodo selecionado.",
                    status=404,
                    content_type="text/plain; charset=utf-8",
                )

            buffer = io.BytesIO()
            adicionados = 0
            hashes = set()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as arquivo:
                for xml_backup in service.iter_xmls(backups):
                    xml = xml_backup.conteudo
                    if not xml.lstrip().startswith("<"):
                        continue
                    digest = hashlib.sha256(xml.encode("utf-8")).digest()
                    if digest in hashes:
                        continue
                    hashes.add(digest)
                    nome = re.sub(
                        r"[^0-9A-Za-z_.-]+",
                        "-",
                        xml_backup.nome,
                    )
                    nome = f"{digest.hex()[:12]}-{nome}"
                    pasta_tipo = classificar_xml_fiscal(
                        xml_backup.nome,
                        xml,
                    )
                    arquivo.writestr(
                        f"focus/{xml_backup.mes}/{pasta_tipo}/{nome}",
                        xml,
                    )
                    adicionados += 1
                arquivo.writestr(
                    "resumo-backup-focus.txt",
                    (
                        "Backup oficial obtido diretamente da Focus NFe\n"
                        f"CNPJ: {filial.cnpj}\n"
                        f"Meses incluidos: {', '.join(backup.mes for backup in backups)}\n"
                        f"Arquivos XML incluidos: {adicionados}\n"
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Falha ao baixar backups mensais da Focus.")
            return HttpResponse(
                f"Nao foi possivel baixar o backup da Focus: {exc}",
                status=502,
                content_type="text/plain; charset=utf-8",
            )

        buffer.seek(0)
        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        periodo = f"{data_inicial or 'disponivel'}-{data_final or 'atual'}"
        response["Content-Disposition"] = (
            f'attachment; filename="backup-focus-{periodo}.zip"'
        )
        return response


class ManifestoFiscalConfigView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'editar'
    template_name = 'fiscal/manifesto/config.html'

    def get_context(self, config):
        return {
            'config': config,
            'prontidao': avaliar_prontidao_dfe(config),
            'producao_liberada': bool(getattr(settings, 'FISCAL_ALLOW_PRODUCTION_ENVIRONMENT', False)),
        }

    def get(self, request):
        config, _ = ManifestoFiscalConfig.objects.get_or_create(
            filial=request.filial_ativa,
            cnpj=request.filial_ativa.cnpj,
            ambiente=ManifestoFiscalConfig.Ambiente.HOMOLOGACAO,
            defaults={'uf': request.filial_ativa.uf, 'ativo': True},
        )
        return render(request, self.template_name, self.get_context(config))

    def post(self, request):
        ambiente = request.POST.get('ambiente') or ManifestoFiscalConfig.Ambiente.HOMOLOGACAO
        if (
            ambiente == ManifestoFiscalConfig.Ambiente.PRODUCAO
            and not getattr(settings, 'FISCAL_ALLOW_PRODUCTION_ENVIRONMENT', False)
        ):
            config, _ = ManifestoFiscalConfig.objects.get_or_create(
                filial=request.filial_ativa,
                cnpj=request.POST.get('cnpj') or request.filial_ativa.cnpj,
                ambiente=ManifestoFiscalConfig.Ambiente.HOMOLOGACAO,
                defaults={'uf': request.POST.get('uf') or request.filial_ativa.uf, 'ativo': True},
            )
            messages.error(
                request,
                'Ambiente de producao bloqueado por seguranca. Use homologacao por enquanto.',
            )
            return render(request, self.template_name, self.get_context(config))

        config, _ = ManifestoFiscalConfig.objects.get_or_create(
            filial=request.filial_ativa,
            cnpj=request.POST.get('cnpj') or request.filial_ativa.cnpj,
            ambiente=ambiente,
            defaults={'uf': request.POST.get('uf') or request.filial_ativa.uf, 'ativo': True},
        )
        config.uf = request.POST.get('uf') or request.filial_ativa.uf
        config.ultimo_nsu = request.POST.get('ultimo_nsu', '').strip()
        certificado = request.FILES.get('certificado_digital')
        if certificado:
            nome = certificado.name or ''
            if not nome.lower().endswith(('.pfx', '.p12')):
                messages.error(request, 'Use um certificado A1 nos formatos .pfx ou .p12.')
                return render(request, self.template_name, self.get_context(config))
            senha = getattr(settings, 'FISCAL_DFE_CERT_PASSWORD', '')
            if senha:
                conteudo = certificado.read()
                certificado.seek(0)
                try:
                    info = validar_certificado_a1_para_config(
                        conteudo,
                        senha,
                        cnpj_esperado=config.cnpj,
                    )
                except DomainError as exc:
                    messages.error(request, str(exc))
                    return render(request, self.template_name, self.get_context(config))
                config.certificado_thumbprint = info.thumbprint
                config.certificado_cnpj = info.cnpj
                config.certificado_titular = info.subject[:255]
                config.certificado_emissor = info.issuer[:255]
                config.certificado_validade_inicio = info.not_before
                config.certificado_validade_fim = info.not_after
            else:
                config.certificado_thumbprint = ''
                config.certificado_cnpj = ''
                config.certificado_titular = ''
                config.certificado_emissor = ''
                config.certificado_validade_inicio = None
                config.certificado_validade_fim = None
                messages.warning(
                    request,
                    'Certificado anexado sem validar conteudo: senha deve ficar apenas em FISCAL_DFE_CERT_PASSWORD.',
                )
            config.certificado_digital = certificado
            config.certificado_nome = nome
        config.save()
        messages.success(request, 'Configuracao do Manifesto Fiscal salva.')
        return redirect('fiscal:manifesto-config')


class ManifestoFiscalAcaoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'editar'

    def post(self, request, pk, acao):
        documento = get_object_or_404(
            ManifestoFiscalDocumento.objects.for_filial(request.filial_ativa),
            pk=pk,
        )
        if acao == 'ciencia':
            ManifestoFiscalService.manifestar_ciencia(documento)
            messages.success(request, 'Ciencia local registrada no ERP. Nenhum evento foi enviado a SEFAZ.')
        elif acao == 'desconhecer':
            ManifestoFiscalService.marcar_desconhecida(documento)
            messages.success(request, 'Operacao marcada localmente como desconhecida. Nenhum evento foi enviado a SEFAZ.')
        elif acao == 'nao-realizada':
            ManifestoFiscalService.marcar_nao_realizada(documento)
            messages.success(request, 'Operacao marcada localmente como nao realizada. Nenhum evento foi enviado a SEFAZ.')
        else:
            messages.error(request, 'Acao invalida.')
        return redirect('fiscal:manifesto-list')


class ManifestoFiscalImportarEntradaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'criar'

    def post(self, request, pk):
        documento = get_object_or_404(
            ManifestoFiscalDocumento.objects.for_filial(request.filial_ativa),
            pk=pk,
        )
        try:
            resultado = ManifestoFiscalService.importar_entrada(documento, request.user)
        except DomainError as exc:
            messages.error(request, str(exc))
            return redirect('fiscal:manifesto-list')

        if resultado.criada:
            messages.success(
                request,
                f'Manifesto importado. NF {resultado.entrada.numero_nf} pronta para conferencia.',
            )
        else:
            messages.info(request, f'Manifesto vinculado a NF {resultado.entrada.numero_nf} ja existente.')
        return redirect('compras:entrada-conferencia', pk=resultado.entrada.pk)


class ManifestoFiscalAnexarXMLView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'criar'
    template_name = 'fiscal/manifesto/anexar_xml.html'

    def get_documento(self, request, pk):
        return get_object_or_404(
            ManifestoFiscalDocumento.objects.for_filial(request.filial_ativa),
            pk=pk,
        )

    def get(self, request, pk):
        documento = self.get_documento(request, pk)
        return render(request, self.template_name, {'documento': documento})

    def post(self, request, pk):
        documento = self.get_documento(request, pk)
        nome_arquivo = ''
        xml_texto = request.POST.get('xml_texto', '')
        arquivo = request.FILES.get('arquivo_xml')
        if arquivo:
            nome_arquivo = arquivo.name
            raw = arquivo.read()
            try:
                xml_texto = raw.decode('utf-8')
            except UnicodeDecodeError:
                xml_texto = raw.decode('latin1')

        try:
            ManifestoFiscalService.anexar_xml_completo(
                documento,
                xml_texto=xml_texto,
                nome_arquivo=nome_arquivo,
            )
        except DomainError as exc:
            messages.error(request, str(exc))
            return render(request, self.template_name, {
                'documento': documento,
                'xml_texto': request.POST.get('xml_texto', ''),
            })

        if request.POST.get('acao') == 'salvar_importar':
            try:
                resultado = ManifestoFiscalService.importar_entrada(documento, request.user)
            except DomainError as exc:
                messages.error(request, str(exc))
                return redirect('fiscal:manifesto-list')
            messages.success(
                request,
                f'XML anexado e NF {resultado.entrada.numero_nf} pronta para conferencia.',
            )
            return redirect('compras:entrada-conferencia', pk=resultado.entrada.pk)

        messages.success(request, 'XML completo anexado ao Manifesto.')
        return redirect('fiscal:manifesto-list')


@csrf_exempt
@require_POST
def webhook_focusnfe(request):
    token_cfg = getattr(settings, 'ERP_FOCUSNFE_WEBHOOK_TOKEN', '')
    if token_cfg and request.GET.get('token') != token_cfg:
        return JsonResponse({'erro': 'token invalido'}, status=403)

    try:
        body = json.loads((request.body or b'{}').decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest('JSON invalido')

    ref = body.get('ref') or request.GET.get('ref', '')
    pk = parse_ref(ref)
    if not pk:
        return JsonResponse({'ok': True, 'ignorado': 'ref ausente ou invalida'})

    documento = DocumentoFiscal.objects.filter(pk=pk).first()
    if not documento:
        return JsonResponse({'ok': True, 'ignorado': 'documento nao encontrado'})

    try:
        FocusNFeService().aplicar_retorno(documento, body)
        from apps.logistica.services.mdfe_focusnfe import (
            processar_nfe_transferencia_autorizada,
            sincronizar_mdfe_por_documento,
        )

        sincronizar_mdfe_por_documento(documento)
        processar_nfe_transferencia_autorizada(documento)
    except Exception:
        logger.exception('Erro ao processar webhook Focus NFe (ref=%s)', ref)
        return JsonResponse({'erro': 'falha ao processar'}, status=500)

    return JsonResponse({'ok': True, 'documento_id': documento.pk, 'status': documento.status})


def _consulta_focus(executar):
    try:
        return JsonResponse(executar(FocusNFeClient()), safe=False)
    except ValueError as exc:
        return JsonResponse({'erro': str(exc)}, status=503)
    except FocusNFeError as exc:
        return JsonResponse(
            {'erro': str(exc), 'detalhe': exc.response_json},
            status=exc.status_code or 502,
        )


@login_required
@require_GET
def consulta_cnpj(request, valor):
    return _consulta_focus(lambda c: c.cnpjs.consultar(valor))


@login_required
@require_GET
def consulta_ncm(request, valor):
    return _consulta_focus(lambda c: c.ncms.consultar(valor))


@login_required
@require_GET
def consulta_cfop(request, valor):
    return _consulta_focus(lambda c: c.cfops.consultar(valor))


@login_required
@require_GET
def consulta_cnae(request, valor):
    return _consulta_focus(lambda c: c.cnaes.consultar(valor))


@login_required
@require_GET
def consulta_municipios_api(request, valor):
    return _consulta_focus(lambda c: c.municipios.consultar(valor))


def _get_pagina(request):
    try:
        return max(1, int(request.GET.get('pagina', 1)))
    except (TypeError, ValueError):
        return 1


def _focus_or_error(fn):
    try:
        return fn(FocusNFeClient()), None
    except ValueError as exc:
        return None, str(exc)
    except FocusNFeError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, f'Erro inesperado: {exc}'


@login_required
def consultas_cfop(request):
    codigo = request.GET.get('codigo', '').strip()
    descricao = request.GET.get('descricao', '').strip()
    pagina = _get_pagina(request)
    resultados, erro = None, None

    if codigo:
        resultados, erro = _focus_or_error(lambda c: c.cfops.consultar(codigo))
        if isinstance(resultados, dict):
            resultados = [resultados]
    elif request.GET:
        resultados, erro = _focus_or_error(lambda c: c.cfops.listar(pagina=pagina))

    return render(request, 'fiscal/consultas/cfop.html', {
        'codigo': codigo,
        'descricao': descricao,
        'pagina': pagina,
        'resultados': resultados,
        'erro': erro,
    })


@login_required
def consultas_cnae(request):
    codigo = request.GET.get('codigo', '').strip()
    descricao = request.GET.get('descricao', '').strip()
    pagina = _get_pagina(request)
    resultados, erro = None, None

    if codigo:
        resultados, erro = _focus_or_error(lambda c: c.cnaes.consultar(codigo))
        if isinstance(resultados, dict):
            resultados = [resultados]
    elif descricao or request.GET.get('buscar'):
        resultados, erro = _focus_or_error(
            lambda c: c.cnaes.listar(descricao=descricao or None, pagina=pagina)
        )

    return render(request, 'fiscal/consultas/cnae.html', {
        'codigo': codigo,
        'descricao': descricao,
        'pagina': pagina,
        'resultados': resultados,
        'erro': erro,
    })


@login_required
def consultas_cnpj_page(request):
    cnpj = request.GET.get('cnpj', '').strip()
    resultado, erro = None, None

    if cnpj:
        resultado, erro = _focus_or_error(lambda c: c.cnpjs.consultar(cnpj))

    return render(request, 'fiscal/consultas/cnpj.html', {
        'cnpj': cnpj,
        'resultado': resultado,
        'erro': erro,
    })


@login_required
def consultas_ncm(request):
    codigo = request.GET.get('codigo', '').strip()
    descricao = request.GET.get('descricao', '').strip()
    pagina = _get_pagina(request)
    resultados, erro = None, None

    if codigo:
        resultados, erro = _focus_or_error(lambda c: c.ncms.consultar(codigo))
        if isinstance(resultados, dict):
            resultados = [resultados]
    elif descricao or request.GET.get('buscar'):
        resultados, erro = _focus_or_error(
            lambda c: c.ncms.listar(descricao=descricao or None, pagina=pagina)
        )

    return render(request, 'fiscal/consultas/ncm.html', {
        'codigo': codigo,
        'descricao': descricao,
        'pagina': pagina,
        'resultados': resultados,
        'erro': erro,
    })


@login_required
def consultas_municipios(request):
    uf = request.GET.get('uf', '').strip().upper()
    nome = request.GET.get('nome', '').strip()
    codigo_ibge = request.GET.get('codigo_ibge', '').strip()
    pagina = _get_pagina(request)
    resultados, erro = None, None

    if codigo_ibge:
        resultados, erro = _focus_or_error(lambda c: c.municipios.consultar(codigo_ibge))
        if isinstance(resultados, dict):
            resultados = [resultados]
    elif uf or nome or request.GET.get('buscar'):
        resultados, erro = _focus_or_error(
            lambda c: c.municipios.listar(uf=uf or None, nome=nome or None, pagina=pagina)
        )

    return render(request, 'fiscal/consultas/municipios.html', {
        'uf': uf,
        'nome': nome,
        'codigo_ibge': codigo_ibge,
        'pagina': pagina,
        'resultados': resultados,
        'erro': erro,
    })
