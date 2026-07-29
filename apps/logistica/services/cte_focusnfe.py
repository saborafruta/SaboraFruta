"""
Servico de emissao CT-e via Focus NFe.

Constroi o payload JSON a partir do modelo CTe e orquestra
emissao, consulta, cancelamento e download do DACTE.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from django.utils import timezone

from apps.financeiro.constants.enums import StatusDocumentoFiscal, TipoDocumentoFiscal
from apps.financeiro.models.fiscal import DocumentoFiscal
from apps.fiscal.integrations.focusnfe.exceptions import FocusNFeError
from apps.fiscal.services.focusnfe_service import FocusNFeService
from apps.logistica.models import CTe, DocumentoCTe

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Mapeamentos choices Django -> codigos Focus NFe
# --------------------------------------------------------------------------

_TIPO_CTE_MAP = {
    CTe.TipoCTe.NORMAL: "0",
    CTe.TipoCTe.COMPLEMENTO: "1",
    CTe.TipoCTe.ANULACAO: "2",
    CTe.TipoCTe.SUBSTITUTO: "3",
}

_TOMADOR_MAP = {
    CTe.Tomador.REMETENTE: "0",
    CTe.Tomador.EXPEDIDOR: "1",
    CTe.Tomador.RECEBEDOR: "2",
    CTe.Tomador.DESTINATARIO: "3",
    CTe.Tomador.OUTROS: "4",
}

_DOC_TIPO_MAP = {
    DocumentoCTe.TipoDocumento.NFE: "1",
    DocumentoCTe.TipoDocumento.NFCE: "2",
    DocumentoCTe.TipoDocumento.CTE: "3",
    DocumentoCTe.TipoDocumento.NFSE: "1",
    DocumentoCTe.TipoDocumento.OUTRO: "99",
}


def _fmt_valor(v) -> str:
    if v is None:
        return "0.00"
    return f"{Decimal(str(v)):.2f}"


def _doc_cnpj_cpf(documento: str) -> Dict[str, str]:
    """Retorna dict com chave cnpj ou cpf conforme tamanho."""
    doc = (documento or "").replace(".", "").replace("-", "").replace("/", "").strip()
    if len(doc) == 14:
        return {"cnpj": doc}
    if len(doc) == 11:
        return {"cpf": doc}
    return {}


def _componentes_valor(cte: CTe) -> list:
    componentes = []
    if cte.valor_frete and cte.valor_frete > 0:
        componentes.append({"nome": "FRETE", "valor": _fmt_valor(cte.valor_frete)})
    if cte.valor_pedagio and cte.valor_pedagio > 0:
        componentes.append({"nome": "PEDAGIO", "valor": _fmt_valor(cte.valor_pedagio)})
    if cte.valor_outros and cte.valor_outros > 0:
        componentes.append({"nome": "OUTROS", "valor": _fmt_valor(cte.valor_outros)})
    if not componentes:
        componentes.append({"nome": "FRETE", "valor": _fmt_valor(cte.valor_frete or 0)})
    return componentes


def _documentos_cte(cte: CTe) -> list:
    """Converte DocumentoCTe -> lista de documentos para o payload Focus NFe."""
    docs = []
    for doc in cte.documentos.all():
        entry: Dict[str, Any] = {
            "tipo_documento": _DOC_TIPO_MAP.get(doc.tipo_documento, "1"),
            "numero": str(doc.numero_documento),
            "serie": str(doc.serie or "1"),
            "valor": _fmt_valor(doc.valor),
            "peso": _fmt_valor(doc.peso_kg),
        }
        if doc.chave_acesso:
            entry["chave"] = doc.chave_acesso[:44]
        docs.append(entry)
    return docs


def construir_payload_cte(cte: CTe) -> Dict[str, Any]:
    """
    Monta o payload JSON para a API Focus NFe (CT-e modal rodoviario).

    Campos nao existentes no modelo (ex: RNTRC, codigo IBGE) recebem
    valores neutros — o usuario deve completar diretamente nos campos
    do formulario de CT-e.
    """
    filial = cte.filial
    filial_cnpj = (filial.cnpj or "").strip()

    # Numero do CT-e: usa numero_cte se for numerico, senao numero interno
    numero_cte = cte.numero
    if cte.numero_cte and cte.numero_cte.isdigit():
        numero_cte = int(cte.numero_cte)

    data_emissao_iso = ""
    if cte.data_emissao:
        data_emissao_iso = f"{cte.data_emissao.isoformat()}T00:00:00-03:00"

    payload: Dict[str, Any] = {
        # Identificacao
        "numero": numero_cte,
        "serie": int(cte.serie or 1),
        "tipo_documento": _TIPO_CTE_MAP.get(cte.tipo_cte, "0"),
        "data_emissao": data_emissao_iso,
        "modal": cte.modal,
        # Operacao
        "cfop": cte.cfop or "6352",
        "natureza_operacao": cte.natureza_operacao or "PRESTACAO DE SERVICOS DE TRANSPORTE",
        "tomador": _TOMADOR_MAP.get(cte.tomador, "0"),
        # Rota
        "municipio_inicio": cte.cidade_origem or "",
        "uf_inicio": cte.uf_origem or "",
        "municipio_fim": cte.cidade_destino or "",
        "uf_fim": cte.uf_destino or "",
        # Valores
        "valor_total_servicos": _fmt_valor(cte.valor_total or cte.valor_frete),
        "valor_receber": _fmt_valor(cte.valor_total or cte.valor_frete),
        "componentes_valor": _componentes_valor(cte),
        # Documentos transportados
        "nfe_info": _documentos_cte(cte),
    }

    # Emitente (filial)
    emit_doc = _doc_cnpj_cpf(filial_cnpj)
    payload.update({
        "emitente_nome": filial.razao_social or "",
        "emitente_logradouro": filial.endereco or "",
        "emitente_numero": filial.numero or "SN",
        "emitente_bairro": filial.bairro or "",
        "emitente_municipio": filial.cidade or "",
        "emitente_uf": filial.uf or "",
        "emitente_cep": (filial.cep or "00000000").replace("-", "")[:8].zfill(8),
        "emitente_pais": "1058",
        "emitente_ie": filial.inscricao_estadual or "",
        **{f"emitente_{k}": v for k, v in emit_doc.items()},
    })

    # Remetente
    rem_doc = _doc_cnpj_cpf(cte.remetente_documento)
    payload.update({
        "remetente_nome": cte.remetente_nome or "NAO INFORMADO",
        "remetente_logradouro": "",
        "remetente_numero": "SN",
        "remetente_bairro": "",
        "remetente_municipio": cte.cidade_origem or "",
        "remetente_uf": cte.uf_origem or "",
        "remetente_cep": "00000000",
        "remetente_pais": "1058",
        **{f"remetente_{k}": v for k, v in rem_doc.items()},
    })

    # Destinatario
    dest_doc = _doc_cnpj_cpf(cte.destinatario_documento)
    payload.update({
        "destinatario_nome": cte.destinatario_nome or "NAO INFORMADO",
        "destinatario_logradouro": "",
        "destinatario_numero": "SN",
        "destinatario_bairro": "",
        "destinatario_municipio": cte.cidade_destino or "",
        "destinatario_uf": cte.uf_destino or "",
        "destinatario_cep": "00000000",
        "destinatario_pais": "1058",
        **{f"destinatario_{k}": v for k, v in dest_doc.items()},
    })

    # Modal rodoviario
    if cte.modal == CTe.Modal.RODOVIARIO:
        placa = (cte.veiculo_placa or "").upper().replace("-", "")
        payload["modal_rodoviario"] = {
            "rntrc": "00000000",
            "placa": placa,
            "uf": cte.uf_origem or "",
        }

    # Motoristas
    if cte.motorista_nome:
        mot_doc = _doc_cnpj_cpf(cte.motorista_documento)
        motorista: Dict[str, str] = {"nome": cte.motorista_nome}
        if mot_doc:
            motorista.update(mot_doc)
        payload["motoristas"] = [motorista]

    return payload


def obter_ou_criar_documento_fiscal(cte: CTe, usuario) -> DocumentoFiscal:
    """Retorna o DocumentoFiscal vinculado ao CT-e, criando um novo se necessario."""
    doc = DocumentoFiscal.objects.filter(origem_tipo="cte", origem_id=cte.pk).first()
    if doc:
        return doc

    filial = cte.filial
    numero_cte = cte.numero
    if cte.numero_cte and cte.numero_cte.isdigit():
        numero_cte = int(cte.numero_cte)
    serie = int(cte.serie or 1)

    dt_emissao = timezone.now()
    if cte.data_emissao:
        dt_emissao = timezone.make_aware(
            timezone.datetime.combine(cte.data_emissao, timezone.datetime.min.time())
        )

    doc = DocumentoFiscal.objects.create(
        filial=filial,
        tipo_documento=TipoDocumentoFiscal.CTE,
        origem_tipo="cte",
        origem_id=cte.pk,
        numero=numero_cte,
        serie=serie,
        emitente_cnpj=filial.cnpj or "",
        destinatario_snapshot={
            "nome": cte.destinatario_nome or "",
            "documento": cte.destinatario_documento or "",
        },
        valor_total=cte.valor_total or Decimal("0"),
        data_emissao=dt_emissao,
        status=StatusDocumentoFiscal.PENDENTE,
        usuario=usuario,
    )
    return doc


def _sincronizar_status_cte(cte: CTe, doc: DocumentoFiscal) -> None:
    """Sincroniza o status do DocumentoFiscal de volta ao CTe."""
    campos = ["updated_at"]

    if doc.status == StatusDocumentoFiscal.AUTORIZADA:
        cte.status = CTe.Status.AUTORIZADO
        campos.append("status")
        if doc.chave:
            cte.chave_acesso = doc.chave
            campos.append("chave_acesso")
        if doc.protocolo:
            cte.protocolo_autorizacao = doc.protocolo
            campos.append("protocolo_autorizacao")
        if doc.data_autorizacao:
            cte.data_autorizacao = doc.data_autorizacao
            campos.append("data_autorizacao")

    elif doc.status == StatusDocumentoFiscal.CANCELADA:
        cte.status = CTe.Status.CANCELADO
        campos.append("status")

    elif doc.status == StatusDocumentoFiscal.DENEGADA:
        cte.status = CTe.Status.DENEGADO
        campos.append("status")

    cte.save(update_fields=list(set(campos)))


# --------------------------------------------------------------------------
# Operacoes principais
# --------------------------------------------------------------------------

def emitir_cte(cte: CTe, usuario) -> Tuple[DocumentoFiscal, str]:
    """
    Emite o CT-e via Focus NFe.
    Retorna (documento_fiscal, mensagem_erro). mensagem_erro vazio = sucesso.
    """
    if cte.status == CTe.Status.AUTORIZADO:
        doc = DocumentoFiscal.objects.filter(origem_tipo="cte", origem_id=cte.pk).first()
        return doc, "CT-e ja autorizado."

    doc = obter_ou_criar_documento_fiscal(cte, usuario)
    payload = construir_payload_cte(cte)
    service = FocusNFeService()

    try:
        doc = service.emitir(doc, payload)
    except FocusNFeError as exc:
        return doc, str(exc)
    except Exception as exc:
        logger.exception("Erro inesperado ao emitir CT-e %s", cte.pk)
        return doc, str(exc)

    _sincronizar_status_cte(cte, doc)
    return doc, ""


def consultar_cte(cte: CTe) -> Tuple[Optional[DocumentoFiscal], str]:
    """Consulta o status do CT-e na Focus NFe e sincroniza o modelo."""
    doc = DocumentoFiscal.objects.filter(origem_tipo="cte", origem_id=cte.pk).first()
    if not doc:
        return None, "CT-e ainda nao foi enviado para emissao."

    service = FocusNFeService()
    try:
        doc = service.consultar(doc)
    except FocusNFeError as exc:
        return doc, str(exc)
    except Exception as exc:
        logger.exception("Erro ao consultar CT-e %s", cte.pk)
        return doc, str(exc)

    _sincronizar_status_cte(cte, doc)
    return doc, ""


def cancelar_cte(
    cte: CTe,
    justificativa: str,
    usuario=None,
) -> Tuple[Optional[DocumentoFiscal], str]:
    """Cancela o CT-e autorizado."""
    doc = DocumentoFiscal.objects.filter(origem_tipo="cte", origem_id=cte.pk).first()
    if not doc:
        return None, "CT-e ainda nao foi enviado para emissao."

    service = FocusNFeService()
    try:
        doc = service.cancelar(doc, justificativa, usuario=usuario)
    except FocusNFeError as exc:
        return doc, str(exc)
    except Exception as exc:
        logger.exception("Erro ao cancelar CT-e %s", cte.pk)
        return doc, str(exc)

    cte.status = CTe.Status.CANCELADO
    cte.save(update_fields=["status", "updated_at"])
    return doc, ""


def dacte_pdf(cte: CTe) -> bytes:
    """Baixa o DACTE em PDF."""
    doc = DocumentoFiscal.objects.filter(origem_tipo="cte", origem_id=cte.pk).first()
    if not doc:
        raise ValueError("CT-e ainda nao foi enviado para emissao.")

    service = FocusNFeService()
    return service.baixar_pdf(doc)
