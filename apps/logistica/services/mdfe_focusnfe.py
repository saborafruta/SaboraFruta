"""
Servico de emissao MDF-e (Manifesto Eletronico de Documentos Fiscais, modelo
58) via Focus NFe.

Constroi o payload JSON a partir do modelo MDFe e orquestra emissao,
consulta, cancelamento, encerramento e download do DAMDFE.

Alguns campos exigidos pela SEFAZ (RENAVAM, tara, capacidade do veiculo,
codigo IBGE do municipio de descarregamento) nao existem hoje no modelo
`MDFe` e recebem valores neutros — complete-os manualmente se a Focus
rejeitar a emissao por um desses campos.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from django.utils import timezone

from apps.financeiro.constants.enums import StatusDocumentoFiscal, TipoDocumentoFiscal
from apps.financeiro.models.fiscal import DocumentoFiscal
from apps.fiscal.integrations.focusnfe.exceptions import FocusNFeError
from apps.fiscal.services.focusnfe_service import FocusNFeService, gerar_ref
from apps.logistica.models import MDFe

logger = logging.getLogger(__name__)


_MODAL_MAP = {
    MDFe.Modal.RODOVIARIO: "1",
    MDFe.Modal.AEREO: "2",
    MDFe.Modal.AQUAVIARIO: "3",
    MDFe.Modal.FERROVIARIO: "4",
}


def _doc_cnpj_cpf(documento: str) -> Dict[str, str]:
    doc = (documento or "").replace(".", "").replace("-", "").replace("/", "").strip()
    if len(doc) == 14:
        return {"cnpj": doc}
    if len(doc) == 11:
        return {"cpf": doc}
    return {}


def _fmt_valor(v) -> str:
    if v is None:
        return "0.00"
    return f"{Decimal(str(v)):.2f}"


def _percurso(mdfe: MDFe) -> list:
    return [
        uf.strip().upper()
        for uf in (mdfe.percurso_ufs or "").split(",")
        if uf.strip()
    ]


def _documentos_por_municipio(mdfe: MDFe) -> list:
    """
    Agrupa os DocumentoMDFe por municipio de descarga — formato exigido
    pela Focus NFe (`municipios_descarregamento`).
    """
    grupos: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for doc in mdfe.documentos.all():
        municipio = (doc.municipio_descarga or mdfe.municipio_descarregamento or "").strip()
        uf = (doc.uf_descarga or mdfe.uf_descarregamento or "").strip().upper()
        chave = (municipio, uf)
        if chave not in grupos:
            grupos[chave] = {
                "municipio_descarregamento": municipio,
                "uf_descarregamento": uf,
                "nfe": [],
                "cte": [],
            }
        if doc.chave_acesso:
            if doc.tipo_documento == "cte":
                grupos[chave]["cte"].append({"chave_cte": doc.chave_acesso})
            else:
                grupos[chave]["nfe"].append({"chave_nfe": doc.chave_acesso})
    return list(grupos.values())


def construir_payload_mdfe(mdfe: MDFe) -> Dict[str, Any]:
    """Monta o payload JSON para a API Focus NFe (MDF-e)."""
    filial = mdfe.filial
    filial_cnpj = (filial.cnpj or "").strip()

    data_emissao_iso = f"{mdfe.data_emissao.isoformat()}T08:00:00-03:00" if mdfe.data_emissao else ""

    payload: Dict[str, Any] = {
        "numero": mdfe.numero,
        "serie": int(mdfe.serie or 1),
        "data_emissao": data_emissao_iso,
        "modal": _MODAL_MAP.get(mdfe.modal, "1"),
        "tipo_emitente": "2",       # 2 = Transportador de Carga Propria (TAC)
        "tipo_transportador": "1",  # 1 = ETC (equiparado)
        "cnpj_emitente": _doc_cnpj_cpf(filial_cnpj).get("cnpj", ""),
        "uf_ini": (mdfe.uf_carregamento or "").strip().upper(),
        "uf_fim": (mdfe.uf_descarregamento or "").strip().upper(),
        "municipio_carregamento": [
            {
                "codigo_municipio": (filial.codigo_municipio_ibge or "").strip(),
                "nome_municipio": (mdfe.municipio_carregamento or filial.cidade or "").strip(),
            }
        ],
        "percurso": _percurso(mdfe),
        "municipios_descarregamento": _documentos_por_municipio(mdfe),
        "valor_total_carga": _float_or_zero(mdfe.valor_total),
        "peso_bruto_total": _float_or_zero(mdfe.peso_total_kg),
        "unidade_medida": "01",  # 01 = KG
        "produto_predominante": "Produtos diversos",
    }

    if mdfe.modal == MDFe.Modal.RODOVIARIO:
        placa = (mdfe.veiculo_placa or "").upper().replace("-", "")
        payload["veiculo_tracao"] = {
            "placa": placa,
            "renavam": "",
            "tara": "0",
            "capacidade_kg": "0",
            "capacidade_m3": "0",
            "tipo_rodado": "01",
            "tipo_carroceria": "00",
            "uf": (mdfe.uf_carregamento or "").strip().upper(),
            "rntrc": (mdfe.veiculo_rntrc or "00000000").strip() or "00000000",
            "condutores": (
                [{
                    "nome": mdfe.motorista_nome,
                    **_doc_cnpj_cpf(mdfe.motorista_cpf),
                }] if mdfe.motorista_nome else []
            ),
        }

    return payload


def _float_or_zero(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def obter_ou_criar_documento_fiscal(mdfe: MDFe, usuario) -> DocumentoFiscal:
    """Retorna o DocumentoFiscal vinculado ao MDF-e, criando um novo se necessario."""
    doc = DocumentoFiscal.objects.filter(origem_tipo="mdfe", origem_id=mdfe.pk).first()
    if doc:
        return doc

    filial = mdfe.filial
    dt_emissao = timezone.now()
    if mdfe.data_emissao:
        dt_emissao = timezone.make_aware(
            timezone.datetime.combine(mdfe.data_emissao, timezone.datetime.min.time())
        )

    doc = DocumentoFiscal.objects.create(
        filial=filial,
        tipo_documento=TipoDocumentoFiscal.MDFE,
        origem_tipo="mdfe",
        origem_id=mdfe.pk,
        numero=mdfe.numero,
        serie=int(mdfe.serie or 1),
        emitente_cnpj=filial.cnpj or "",
        destinatario_snapshot={},
        valor_total=mdfe.valor_total or Decimal("0"),
        data_emissao=dt_emissao,
        status=StatusDocumentoFiscal.PENDENTE,
        usuario=usuario,
    )
    return doc


def _sincronizar_status_mdfe(mdfe: MDFe, doc: DocumentoFiscal) -> None:
    """Sincroniza o status do DocumentoFiscal de volta ao MDFe."""
    campos = ["updated_at"]

    if doc.status == StatusDocumentoFiscal.AUTORIZADA:
        mdfe.status = MDFe.Status.AUTORIZADO
        campos.append("status")
        if doc.chave:
            mdfe.chave_acesso = doc.chave
            campos.append("chave_acesso")
        if doc.protocolo:
            mdfe.protocolo_autorizacao = doc.protocolo
            campos.append("protocolo_autorizacao")
        if doc.data_autorizacao:
            mdfe.data_autorizacao = doc.data_autorizacao
            campos.append("data_autorizacao")

    elif doc.status == StatusDocumentoFiscal.CANCELADA:
        mdfe.status = MDFe.Status.CANCELADO
        campos.append("status")

    mdfe.save(update_fields=list(set(campos)))


# --------------------------------------------------------------------------
# Operacoes principais
# --------------------------------------------------------------------------

def emitir_mdfe(mdfe: MDFe, usuario) -> Tuple[DocumentoFiscal, str]:
    """
    Emite o MDF-e via Focus NFe.
    Retorna (documento_fiscal, mensagem_erro). mensagem_erro vazio = sucesso.
    """
    if mdfe.status == MDFe.Status.AUTORIZADO:
        doc = DocumentoFiscal.objects.filter(origem_tipo="mdfe", origem_id=mdfe.pk).first()
        return doc, "MDF-e ja autorizado."

    if not mdfe.documentos.exists():
        return None, "Vincule ao menos um documento (NF-e/CT-e) antes de emitir o MDF-e."

    doc = obter_ou_criar_documento_fiscal(mdfe, usuario)
    payload = construir_payload_mdfe(mdfe)
    service = FocusNFeService()

    try:
        doc = service.emitir(doc, payload)
    except FocusNFeError as exc:
        return doc, str(exc)
    except Exception as exc:
        logger.exception("Erro inesperado ao emitir MDF-e %s", mdfe.pk)
        return doc, str(exc)

    _sincronizar_status_mdfe(mdfe, doc)
    return doc, ""


def consultar_mdfe(mdfe: MDFe) -> Tuple[Optional[DocumentoFiscal], str]:
    """Consulta o status do MDF-e na Focus NFe e sincroniza o modelo."""
    doc = DocumentoFiscal.objects.filter(origem_tipo="mdfe", origem_id=mdfe.pk).first()
    if not doc:
        return None, "MDF-e ainda nao foi enviado para emissao."

    service = FocusNFeService()
    try:
        doc = service.consultar(doc)
    except FocusNFeError as exc:
        return doc, str(exc)
    except Exception as exc:
        logger.exception("Erro ao consultar MDF-e %s", mdfe.pk)
        return doc, str(exc)

    _sincronizar_status_mdfe(mdfe, doc)
    return doc, ""


def cancelar_mdfe(mdfe: MDFe, justificativa: str) -> Tuple[Optional[DocumentoFiscal], str]:
    """Cancela o MDF-e autorizado."""
    doc = DocumentoFiscal.objects.filter(origem_tipo="mdfe", origem_id=mdfe.pk).first()
    if not doc:
        return None, "MDF-e ainda nao foi enviado para emissao."

    service = FocusNFeService()
    try:
        doc = service.cancelar(doc, justificativa)
    except FocusNFeError as exc:
        return doc, str(exc)
    except Exception as exc:
        logger.exception("Erro ao cancelar MDF-e %s", mdfe.pk)
        return doc, str(exc)

    mdfe.status = MDFe.Status.CANCELADO
    mdfe.save(update_fields=["status", "updated_at"])
    return doc, ""


def encerrar_mdfe(mdfe: MDFe, codigo_municipio: str, uf: str) -> Tuple[Optional[DocumentoFiscal], str]:
    """
    Encerra o MDF-e (transporte chegou ao destino). Diferente de
    emitir/cancelar, o encerramento nao muda o status do DocumentoFiscal
    (o documento continua "autorizada") — apenas o MDFe.status avanca
    para "encerrado".
    """
    doc = DocumentoFiscal.objects.filter(origem_tipo="mdfe", origem_id=mdfe.pk).first()
    if not doc:
        return None, "MDF-e ainda nao foi enviado para emissao."
    if mdfe.status != MDFe.Status.AUTORIZADO:
        return doc, "Somente um MDF-e autorizado pode ser encerrado."

    service = FocusNFeService()
    try:
        retorno = service.client.mdfe.encerrar(
            gerar_ref(doc), codigo_municipio=codigo_municipio, uf=uf,
        )
    except FocusNFeError as exc:
        return doc, str(exc)
    except Exception as exc:
        logger.exception("Erro ao encerrar MDF-e %s", mdfe.pk)
        return doc, str(exc)

    status_focus = str((retorno or {}).get("status") or "").lower()
    if status_focus not in ("encerrado", "encerrada"):
        mensagem = str(
            (retorno or {}).get("mensagem_sefaz")
            or (retorno or {}).get("mensagem")
            or "A Focus nao confirmou o encerramento do MDF-e."
        )
        return doc, mensagem

    mdfe.status = MDFe.Status.ENCERRADO
    mdfe.data_encerramento = timezone.localdate()
    mdfe.save(update_fields=["status", "data_encerramento", "updated_at"])
    return doc, ""


def damdfe_pdf(mdfe: MDFe) -> bytes:
    """Baixa o DAMDFE em PDF."""
    doc = DocumentoFiscal.objects.filter(origem_tipo="mdfe", origem_id=mdfe.pk).first()
    if not doc:
        raise ValueError("MDF-e ainda nao foi enviado para emissao.")

    service = FocusNFeService()
    return service.baixar_pdf(doc)
