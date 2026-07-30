"""
Emissão de NF-e (modelo 55) de Transferência de Mercadoria entre filiais.

Reaproveita a engine fiscal do PDV (cálculo de ICMS/PIS/COFINS/IPI por item,
formatação de valores e regras MEI) construindo um payload FocusNFe cujo
emitente é a filial de origem e cujo destinatário é a filial de destino
(mesmo titular, CNPJ distinto).

Regras aplicadas:
- Base de cálculo de cada item = custo médio do estoque na origem.
- CFOP 5152 (mesma UF) ou 6152 (UF diferente) — transferência de mercadoria.
- Natureza de operação: "Transferencia de mercadoria".
- A emissão é assíncrona: a Focus responde "processando" e o status final
  chega via webhook. A movimentação de estoque NÃO é revertida se a nota
  falhar (o erro é reportado para reemissão posterior).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
from typing import Any, Dict, List

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.financeiro.constants.enums import StatusDocumentoFiscal, TipoDocumentoFiscal
from apps.financeiro.models import DocumentoFiscal
from apps.pdv.services.nfce_payload_builder import (
    _data_emissao_brt,
    _decimal,
    _dinheiro,
    _float_dinheiro,
    _montar_item_fiscal,
    _somente_digitos,
    _validar_regras_mei,
)


class _ItemTransfAdapter:
    """Objeto mínimo com a interface que `_montar_item_fiscal` espera."""

    def __init__(self, produto, quantidade: Decimal, valor_unitario: Decimal):
        self.produto = produto
        self.quantidade = quantidade
        self.valor_unitario = valor_unitario
        self.desconto_valor = Decimal("0")
        self.acrescimo_valor = Decimal("0")
        unidade = produto.unidade_medida.sigla if produto.unidade_medida_id else "UN"
        descricao = (produto.descricao_pdv or produto.descricao or "").upper()
        if unidade.upper() == "G" and re.search(r"\b\d+\s*G\b", descricao):
            unidade = "UN"
        self.unidade_medida = unidade


_CHAVES_TRIBUTOS_TRANSFERENCIA_REMOVER = {
    "icms_modalidade_base_calculo", "icms_reducao_base_calculo",
    "icms_base_calculo", "icms_aliquota", "icms_valor",
    "icms_aliquota_credito_simples", "icms_valor_credito_simples",
    "fcp_base_calculo", "fcp_percentual", "fcp_valor",
    "ibs_cbs_base_calculo", "ibs_uf_aliquota", "ibs_mun_aliquota",
    "ibs_uf_percentual_reducao_aliquota", "ibs_uf_aliquota_efetiva",
    "ibs_mun_percentual_reducao_aliquota", "ibs_mun_aliquota_efetiva",
    "ibs_uf_valor", "ibs_mun_valor", "ibs_valor_total",
    "cbs_aliquota", "cbs_percentual_reducao_aliquota",
    "cbs_aliquota_efetiva", "cbs_valor",
    "is_situacao_tributaria", "is_classificacao_tributaria",
    "is_base_calculo", "is_aliquota", "is_valor",
}


def _aplicar_tributacao_transferencia(item: Dict[str, Any]) -> None:
    """Aplica a tributacao da transferencia entre estabelecimentos do titular."""
    for chave in _CHAVES_TRIBUTOS_TRANSFERENCIA_REMOVER:
        item.pop(chave, None)

    item["icms_situacao_tributaria"] = "400"
    item["pis_situacao_tributaria"] = "08"
    item["pis_base_calculo"] = 0.0
    item["pis_valor"] = 0.0
    item.pop("pis_aliquota_porcentual", None)
    item["cofins_situacao_tributaria"] = "08"
    item["cofins_base_calculo"] = 0.0
    item["cofins_valor"] = 0.0
    item.pop("cofins_aliquota_porcentual", None)
    item["ibs_cbs_situacao_tributaria"] = "410"
    item["ibs_cbs_classificacao_tributaria"] = "410002"


def _cfop_transferencia(
    uf_origem: str,
    uf_destino: str,
    origem_mercadoria: str = "producao_propria",
) -> str:
    """Define o CFOP pela UF e pela origem comercial da mercadoria."""
    uo = (uf_origem or "").strip().upper()
    ud = (uf_destino or "").strip().upper()
    interestadual = bool(uo and ud and uo != ud)
    if origem_mercadoria == "producao_propria":
        return "6151" if interestadual else "5151"
    if origem_mercadoria == "terceiros":
        return "6152" if interestadual else "5152"
    raise DadosInvalidosError(
        "Informe se os produtos são de produção própria ou adquiridos de terceiros."
    )


def _local_destino_transferencia(uf_origem: str, uf_destino: str) -> str:
    uo = (uf_origem or "").strip().upper()
    ud = (uf_destino or "").strip().upper()
    return "2" if uo and ud and uo != ud else "1"


def _aplicar_destinatario_filial(payload: Dict[str, Any], filial_destino) -> None:
    """Preenche os campos de destinatário a partir da filial de destino."""
    cnpj = _somente_digitos(filial_destino.cnpj)
    if len(cnpj) != 14:
        raise DadosInvalidosError(
            "A filial de destino não possui CNPJ válido para emissão de NF-e."
        )
    payload["nome_destinatario"] = filial_destino.razao_social
    payload["cnpj_destinatario"] = cnpj

    ie = (filial_destino.inscricao_estadual or "").strip()
    if ie and ie.upper() != "ISENTO":
        payload["inscricao_estadual_destinatario"] = ie
        payload["indicador_inscricao_estadual_destinatario"] = "1"
    elif ie.upper() == "ISENTO":
        payload["indicador_inscricao_estadual_destinatario"] = "2"
    else:
        payload["indicador_inscricao_estadual_destinatario"] = "9"

    endereco = {
        "logradouro": (filial_destino.endereco or "").strip(),
        "numero": (filial_destino.numero or "").strip(),
        "bairro": (filial_destino.bairro or "").strip(),
        "municipio": (filial_destino.cidade or "").strip(),
        "uf": (filial_destino.uf or "").strip().upper(),
        "cep": _somente_digitos(filial_destino.cep),
        "codigo_municipio": _somente_digitos(filial_destino.codigo_municipio_ibge),
    }
    faltando = [
        label
        for campo, label in (
            ("logradouro", "logradouro"),
            ("numero", "número"),
            ("bairro", "bairro"),
            ("municipio", "município"),
            ("uf", "UF"),
        )
        if not endereco[campo]
    ]
    if faltando:
        raise DadosInvalidosError(
            "NF-e de transferência exige endereço completo da filial de destino. "
            f"Complete no cadastro da filial: {', '.join(faltando)}."
        )

    payload["logradouro_destinatario"] = endereco["logradouro"][:60]
    payload["numero_destinatario"] = (endereco["numero"] or "SN")[:60]
    if (filial_destino.complemento or "").strip():
        payload["complemento_destinatario"] = filial_destino.complemento.strip()[:60]
    payload["bairro_destinatario"] = endereco["bairro"][:60]
    payload["municipio_destinatario"] = endereco["municipio"][:60]
    payload["uf_destinatario"] = endereco["uf"][:2]
    if endereco["cep"]:
        payload["cep_destinatario"] = endereco["cep"][:8]
    if len(endereco["codigo_municipio"]) == 7:
        payload["codigo_municipio_destinatario"] = endereco["codigo_municipio"]


def construir_payload_transferencia(
    filial_origem,
    filial_destino,
    itens: List[_ItemTransfAdapter],
    numero_nfe: int,
    serie_nfe: int,
    observacao: str = "",
    origem_mercadoria: str = "producao_propria",
) -> Dict[str, Any]:
    if not itens:
        raise DadosInvalidosError("Nenhum item para emitir a NF-e de transferência.")

    data_emissao_iso = _data_emissao_brt(timezone.now())
    data_emissao = date.fromisoformat(data_emissao_iso[:10])

    uf_origem = (filial_origem.uf or "").strip().upper()
    uf_destino = (filial_destino.uf or "").strip().upper()
    cfop = _cfop_transferencia(uf_origem, uf_destino, origem_mercadoria)
    local_destino = _local_destino_transferencia(uf_origem, uf_destino)

    items: List[dict] = []
    for i, item in enumerate(itens):
        item_payload = _montar_item_fiscal(
            i + 1,
            item,
            filial_origem,
            local_destino,
            data_emissao,
            Decimal("0"),
            Decimal("0"),
        )
        # Transferência sempre usa CFOP 5152/6152, independente do CFOP de venda.
        item_payload["cfop"] = cfop
        _aplicar_tributacao_transferencia(item_payload)
        items.append(item_payload)

    cnpj_emitente = _somente_digitos(filial_origem.cnpj)

    payload: Dict[str, Any] = {
        "cnpj_emitente": cnpj_emitente,
        "natureza_operacao": "Transferencia de mercadoria",
        "numero": numero_nfe,
        "serie": str(serie_nfe),
        "data_emissao": data_emissao_iso,
        "data_entrada_saida": data_emissao_iso,
        "tipo_documento": "1",          # 1 = saída
        "finalidade_emissao": "1",      # 1 = normal
        "consumidor_final": 0,
        "presenca_comprador": "9",      # 9 = operação não presencial (outros)
        "local_destino": local_destino,
        "modalidade_frete": "3",        # 3 = transporte proprio por conta do remetente
        "items": items,
        # 90 = Sem pagamento (transferência não tem cobrança financeira)
        "formas_pagamento": [{"forma_pagamento": "90", "valor_pagamento": 0.0}],
    }
    _aplicar_destinatario_filial(payload, filial_destino)

    # ── Totais fiscais ──────────────────────────────────────────────
    valor_produtos = sum((_decimal(it["valor_bruto"]) for it in items), Decimal("0"))
    valor_outras = sum((_decimal(it.get("valor_outras_despesas", 0)) for it in items), Decimal("0"))
    valor_st = sum((_decimal(it.get("icms_valor_st", 0)) for it in items), Decimal("0"))
    valor_ipi = sum((_decimal(it.get("ipi_valor", 0)) for it in items), Decimal("0"))
    valor_total = _dinheiro(valor_produtos + valor_outras + valor_st + valor_ipi)

    if valor_total <= 0:
        raise DadosInvalidosError(
            "O custo dos produtos é zero. Cadastre o custo médio antes de emitir "
            "a NF-e de transferência."
        )

    payload["valor_produtos"] = _float_dinheiro(valor_produtos)
    payload["valor_desconto"] = 0.0
    payload["valor_outras_despesas"] = _float_dinheiro(valor_outras)
    payload["valor_total"] = _float_dinheiro(valor_total)

    obs = " ".join(str(observacao or "").split()).strip()
    info = (
        "Transferencia de mercadoria entre estabelecimentos do mesmo titular. "
        "Transporte de carga propria por conta do emitente."
    )
    if obs:
        info = f"{info} {obs}"
    payload["informacoes_adicionais_contribuinte"] = info[:5000]

    _validar_regras_mei(filial_origem, items, modelo=55, local_destino=local_destino)

    return payload


@transaction.atomic
def emitir_nfe_transferencia(
    filial_origem,
    filial_destino,
    itens: List[dict],
    usuario,
    *,
    origem_id: int | None = None,
    observacao: str = "",
    origem_mercadoria: str = "producao_propria",
) -> DocumentoFiscal:
    """
    Reserva número/série, cria o DocumentoFiscal e dispara a emissão na SEFAZ.

    `itens` é uma lista de dicts: {'produto': Produto, 'quantidade': Decimal,
    'custo_unitario': Decimal}.
    """
    from apps.core.models.parametros import ParametroDocumentoFiscal, ParametrosSistema
    from apps.fiscal.integrations.focusnfe import FocusNFeClient
    from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig
    from apps.fiscal.services.focusnfe_service import FocusNFeService

    token = (getattr(filial_origem, "focusnfe_token", "") or "").strip()
    if not token:
        raise DadosInvalidosError(
            "Configure o Token Focus NFe da filial de origem antes de emitir a "
            "NF-e de transferência."
        )

    cnpj_origem = _somente_digitos(filial_origem.cnpj)
    cnpj_destino = _somente_digitos(filial_destino.cnpj)
    if len(cnpj_origem) != 14 or len(cnpj_destino) != 14:
        raise DadosInvalidosError("Origem e destino precisam ter CNPJ válido.")
    if cnpj_origem[:8] != cnpj_destino[:8]:
        raise DadosInvalidosError(
            "A NF-e de transferência só pode ser emitida entre matriz e filial do mesmo titular."
        )

    adapters = [
        _ItemTransfAdapter(
            it["produto"],
            _decimal(it["quantidade"]),
            _dinheiro(it["custo_unitario"]),
        )
        for it in itens
    ]

    # ── Reserva atômica de número/série (mesma estratégia do PDV) ────
    params, _ = ParametrosSistema.objects.get_or_create(filial=filial_origem)
    doc_params = (
        ParametroDocumentoFiscal.objects
        .select_for_update()
        .filter(parametros=params, tipo_documento="nfe")
        .first()
    )
    if doc_params:
        numero_nfe = doc_params.proximo_numero
        serie_nfe = doc_params.serie or 1
        doc_params.proximo_numero = numero_nfe + 1
        doc_params.save(update_fields=["proximo_numero"])
    else:
        from apps.core.models.empresa import Filial as _Filial
        filial_lock = _Filial.objects.select_for_update().get(pk=filial_origem.pk)
        numero_nfe = filial_lock.proximo_numero_nfe
        serie_nfe = filial_lock.serie_nfe or 1
        filial_lock.proximo_numero_nfe = numero_nfe + 1
        filial_lock.save(update_fields=["proximo_numero_nfe"])

    payload = construir_payload_transferencia(
        filial_origem,
        filial_destino,
        adapters,
        numero_nfe,
        serie_nfe,
        observacao,
        origem_mercadoria,
    )

    doc = DocumentoFiscal.objects.create(
        filial=filial_origem,
        tipo_documento=TipoDocumentoFiscal.NFE,
        origem_tipo="transferencia_estoque",
        origem_id=origem_id,
        numero=numero_nfe,
        serie=serie_nfe,
        natureza_operacao_descricao="Transferencia de mercadoria",
        tipo_operacao="1",
        finalidade_nfe=1,
        modalidade_frete=3,
        emitente_cnpj=filial_origem.cnpj,
        destinatario_tipo="filial",
        destinatario_id=filial_destino.pk,
        destinatario_snapshot={
            "nome": filial_destino.razao_social,
            "cpf_cnpj": _somente_digitos(filial_destino.cnpj),
            "logradouro": filial_destino.endereco,
            "numero": filial_destino.numero,
            "complemento": filial_destino.complemento,
            "bairro": filial_destino.bairro,
            "cidade": filial_destino.cidade,
            "uf": filial_destino.uf,
            "cep": filial_destino.cep,
            "codigo_municipio_ibge": filial_destino.codigo_municipio_ibge,
        },
        valor_produtos=_decimal(payload["valor_produtos"]),
        valor_desconto=_decimal(payload["valor_desconto"]),
        valor_total=_decimal(payload["valor_total"]),
        status=StatusDocumentoFiscal.PENDENTE,
        data_emissao=timezone.now(),
        usuario=usuario,
    )

    ambiente = getattr(filial_origem, "focusnfe_ambiente", None)
    config = FocusNFeConfig.from_env(token=token, ambiente=ambiente)
    client = FocusNFeClient(config=config)
    service = FocusNFeService(client=client)

    return service.emitir(doc, payload)


def cancelar_nfe_transferencia(
    documento_fiscal,
    justificativa: str,
    usuario=None,
) -> DocumentoFiscal:
    """
    Cancela (via Focus/SEFAZ) uma NF-e de transferência já autorizada.
    Só é possível cancelar documentos com status "autorizada" — a Focus
    rejeita a tentativa caso contrário.
    """
    from apps.fiscal.integrations.focusnfe import FocusNFeClient
    from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig
    from apps.fiscal.services.focusnfe_service import FocusNFeService

    justificativa = (justificativa or "").strip()
    if len(justificativa) < 15:
        raise DadosInvalidosError(
            "A justificativa de cancelamento deve ter ao menos 15 caracteres."
        )
    if documento_fiscal.status != StatusDocumentoFiscal.AUTORIZADA:
        raise DadosInvalidosError(
            "Somente uma NF-e autorizada pode ser cancelada."
        )

    filial = documento_fiscal.filial
    token = (getattr(filial, "focusnfe_token", "") or "").strip()
    if not token:
        raise DadosInvalidosError(
            "Configure o Token Focus NFe da filial antes de cancelar a nota."
        )

    ambiente = getattr(filial, "focusnfe_ambiente", None)
    config = FocusNFeConfig.from_env(token=token, ambiente=ambiente)
    client = FocusNFeClient(config=config)
    service = FocusNFeService(client=client)

    return service.cancelar(documento_fiscal, justificativa, usuario=usuario)
