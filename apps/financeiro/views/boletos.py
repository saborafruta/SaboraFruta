"""Operação de remessa e retorno de boletos."""
from datetime import timedelta

from django.contrib import messages
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.tenant_context import get_current_database_alias
from apps.financeiro.constants.enums import StatusContaReceber, TipoFormaPagamento
from apps.financeiro.models import (
    Boleto, ContaBancaria, ContaReceber, RemessaBancaria, RetornoBancario,
)
from apps.financeiro.services.boleto_pdf import gerar_pdf_boleto, validar_pdf_boleto
from apps.financeiro.services.cnab_cobranca import RemessaCobrancaService, RetornoCNABService
from apps.whatsapp_agent.cobranca_service import agendar_cobrancas_manuais
from apps.whatsapp_agent.models import EnvioCobrancaWhatsApp
from apps.whatsapp_agent.notifications import configuracao_whatsapp_conectada


def _boleto_pdf_disponivel(boleto):
    disponivel, _motivo = validar_pdf_boleto(boleto)
    return disponivel


class BoletoComunicacaoListView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "ver"
    template_name = "financeiro/boletos/comunicacao.html"

    def get(self, request):
        filial = request.filial_ativa
        contas = ContaBancaria.objects.filter(filial=filial, ativo=True).order_by("banco_nome", "descricao")
        conta_id = request.GET.get("conta_bancaria")
        conta = contas.filter(pk=conta_id).first() if conta_id else contas.first()
        data_ini = request.GET.get("data_ini", "")
        data_fim = request.GET.get("data_fim", "")
        periodo = request.GET.get("periodo", "")
        if periodo not in {"vencidos", "hoje", "proximos_7"}:
            periodo = ""
        titulos = ContaReceber.objects.none()
        if conta:
            titulos_qs = ContaReceber.objects.filter(
                filial=filial,
                status__in=[StatusContaReceber.ABERTO, StatusContaReceber.VENCIDO, StatusContaReceber.NEGOCIADO],
                forma_pagamento__tipo=TipoFormaPagamento.BOLETO,
            ).filter(Q(conta_bancaria=conta) | Q(conta_bancaria__isnull=True))
            hoje = timezone.localdate()
            if periodo == "vencidos":
                titulos_qs = titulos_qs.filter(data_vencimento__lt=hoje)
            elif periodo == "hoje":
                titulos_qs = titulos_qs.filter(data_vencimento=hoje)
            elif periodo == "proximos_7":
                titulos_qs = titulos_qs.filter(
                    data_vencimento__gte=hoje,
                    data_vencimento__lte=hoje + timedelta(days=7),
                )
            else:
                if data_ini:
                    titulos_qs = titulos_qs.filter(data_vencimento__gte=data_ini)
                if data_fim:
                    titulos_qs = titulos_qs.filter(data_vencimento__lte=data_fim)
            titulos = list(titulos_qs.select_related("cliente").order_by("data_vencimento")[:300])
            boletos = {}
            for boleto in Boleto.objects.filter(
                conta_receber_id__in=[titulo.pk for titulo in titulos],
                conta_bancaria=conta,
            ).order_by("conta_receber_id", "-created_at"):
                boletos.setdefault(boleto.conta_receber_id, boleto)
            for titulo in titulos:
                titulo.boleto_ativo = boletos.get(titulo.pk)
                if titulo.boleto_ativo:
                    disponivel, motivo = validar_pdf_boleto(titulo.boleto_ativo)
                    titulo.boleto_ativo.pdf_disponivel = disponivel
                    titulo.boleto_ativo.pdf_indisponivel_motivo = motivo
                    titulo.boleto_ativo.link_publico = ""
                    if titulo.boleto_ativo.url_boleto:
                        titulo.boleto_ativo.link_publico = request.build_absolute_uri(reverse(
                            "boleto_publico:visualizar", args=[titulo.boleto_ativo.public_token],
                        ))
        remessas = list(
            RemessaBancaria.objects.filter(filial=filial)
            .select_related("conta_bancaria", "usuario")
            .prefetch_related(Prefetch(
                "boleto_set",
                queryset=Boleto.objects.select_related("conta_bancaria", "conta_receber").order_by("pk"),
                to_attr="boletos_historico",
            ))
            .order_by("-created_at")[:20]
        )
        for remessa in remessas:
            remessa.boletos_pdf = [
                boleto for boleto in remessa.boletos_historico if _boleto_pdf_disponivel(boleto)
            ]
        alias = get_current_database_alias()
        configuracao_whatsapp = configuracao_whatsapp_conectada(filial, alias)
        return render(request, self.template_name, {
            "title": "Boletos · Remessa e Retorno", "contas_bancarias": contas,
            "conta_bancaria": conta, "titulos": titulos,
            "data_ini": data_ini, "data_fim": data_fim, "periodo_filtro": periodo,
            "aba_inicial": request.GET.get("aba") if request.GET.get("aba") in {
                "remessa", "retorno", "historico", "whatsapp",
            } else "remessa",
            "configuracao_whatsapp": configuracao_whatsapp,
            "envios_cobranca": EnvioCobrancaWhatsApp.objects.using('default').filter(
                tenant_alias=alias, filial_cnpj=filial.cnpj,
            ).order_by('-created_at')[:50],
            "remessas": remessas,
            "retornos": RetornoBancario.objects.filter(filial=filial).select_related("conta_bancaria", "usuario").order_by("-created_at")[:20],
        })


class BoletoWhatsAppAgendarView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "editar"

    def post(self, request):
        filial = request.filial_ativa
        ids = request.POST.getlist('titulos')
        conta_id = request.POST.get('conta_bancaria')
        titulos = ContaReceber.objects.filter(
            pk__in=ids,
            filial=filial,
            forma_pagamento__tipo=TipoFormaPagamento.BOLETO,
            status__in=[
                StatusContaReceber.ABERTO,
                StatusContaReceber.PAGO_PARCIAL,
                StatusContaReceber.VENCIDO,
                StatusContaReceber.NEGOCIADO,
            ],
            valor_saldo__gt=0,
        )
        if conta_id:
            titulos = titulos.filter(Q(conta_bancaria_id=conta_id) | Q(conta_bancaria__isnull=True))
        if not ids:
            messages.error(request, 'Selecione ao menos um boleto.')
        else:
            resultado = agendar_cobrancas_manuais(
                titulos,
                usuario=request.user,
                registrar_autorizacao=request.POST.get('confirmar_consentimento') == 'on',
            )
            if resultado['criados']:
                messages.success(
                    request,
                    f"{resultado['criados']} aviso(s) colocado(s) na fila do WhatsApp.",
                )
            if resultado['ignorados']:
                messages.warning(
                    request,
                    f"{resultado['ignorados']} título(s) ignorado(s): confira telefone, consentimento, conexão ou duplicidade.",
                )
        destino = reverse('financeiro:boleto_comunicacao') + '?aba=whatsapp'
        if conta_id:
            destino += f'&conta_bancaria={conta_id}'
        return redirect(destino)


class BoletoWhatsAppExcluirAgendamentoView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "editar"

    def post(self, request, pk):
        filial = request.filial_ativa
        alias = get_current_database_alias()
        with transaction.atomic(using='default'):
            envio = get_object_or_404(
                EnvioCobrancaWhatsApp.objects.using('default').select_for_update(),
                pk=pk,
                tenant_alias=alias,
                filial_cnpj=filial.cnpj,
            )
            if envio.status != EnvioCobrancaWhatsApp.Status.PENDENTE:
                messages.error(
                    request,
                    'Somente agendamentos pendentes podem ser excluídos.',
                )
            else:
                envio.delete(using='default')
                messages.success(request, 'Agendamento do WhatsApp excluído da fila.')
        return redirect(reverse('financeiro:boleto_comunicacao') + '?aba=whatsapp')


class BoletoRemessaGerarView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "criar"

    def post(self, request):
        filial = request.filial_ativa
        conta = get_object_or_404(ContaBancaria, pk=request.POST.get("conta_bancaria"), filial=filial, ativo=True)
        ids = request.POST.getlist("titulos")
        try:
            remessa = RemessaCobrancaService.gerar(conta_bancaria=conta, titulos=ids, usuario=request.user)
        except DomainError as exc:
            messages.error(request, str(exc))
        else:
            aviso = " Arquivo ainda não homologado pelo banco." if not conta.remessa_homologada else ""
            messages.success(request, f"Remessa {remessa.numero_remessa} gerada com {remessa.quantidade_boletos} boleto(s).{aviso}")
        return redirect(reverse("financeiro:boleto_comunicacao") + f"?conta_bancaria={conta.pk}")


class BoletoRetornoImportarView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "editar"

    def post(self, request):
        filial = request.filial_ativa
        conta = get_object_or_404(ContaBancaria, pk=request.POST.get("conta_bancaria"), filial=filial, ativo=True)
        arquivo = request.FILES.get("arquivo")
        if not arquivo:
            messages.error(request, "Selecione o arquivo de retorno.")
        else:
            try:
                retorno = RetornoCNABService.importar(
                    conta_bancaria=conta, nome=arquivo.name, dados=arquivo.read(), usuario=request.user,
                )
            except DomainError as exc:
                messages.error(request, str(exc))
            else:
                r = retorno.resumo_processamento
                messages.success(request, f"Retorno processado: {r['pagos']} pago(s), {r['rejeitados']} rejeitado(s) e {r['nao_encontrados']} não localizado(s).")
        return redirect(reverse("financeiro:boleto_comunicacao") + f"?conta_bancaria={conta.pk}")


class RemessaDownloadView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "ver"

    def get(self, request, pk):
        remessa = get_object_or_404(RemessaBancaria, pk=pk, filial=request.filial_ativa)
        if not remessa.arquivo_cnab:
            raise Http404
        resposta = HttpResponse(remessa.arquivo_cnab.encode("ascii"), content_type="text/plain; charset=us-ascii")
        resposta["Content-Disposition"] = f'attachment; filename="{remessa.arquivo_nome}"'
        return resposta


class BoletoPdfInternoView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "ver"

    def get(self, request, pk):
        boleto = get_object_or_404(
            Boleto.objects.select_related(
                "filial__empresa", "conta_bancaria", "conta_receber__cliente",
            ),
            pk=pk, filial=request.filial_ativa,
        )
        try:
            conteudo = gerar_pdf_boleto(boleto)
        except DomainError as exc:
            return HttpResponseBadRequest(str(exc))
        resposta = HttpResponse(conteudo, content_type="application/pdf")
        resposta["Content-Disposition"] = f'attachment; filename="boleto-{boleto.seu_numero or boleto.pk}.pdf"'
        resposta["Cache-Control"] = "private, no-store"
        return resposta


class RetornoDetalheView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "ver"

    def get(self, request, pk):
        retorno = get_object_or_404(
            RetornoBancario.objects.select_related("conta_bancaria"), pk=pk, filial=request.filial_ativa,
        )
        itens = list(retorno.itens.select_related(
            "boleto", "boleto__conta_bancaria", "boleto__conta_receber", "boleto__conta_receber__cliente",
        ))
        for item in itens:
            item.pdf_disponivel = _boleto_pdf_disponivel(item.boleto)
        return render(request, "financeiro/boletos/retorno_detalhe.html", {
            "title": f"Retorno {retorno.arquivo_nome}", "retorno": retorno,
            "itens": itens,
        })
