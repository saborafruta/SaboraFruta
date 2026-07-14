"""
Construtor de payload NFC-e / NF-e para Focus NFe — Fase 2 (PDV).

Constrói o dicionário JSON pronto para envio via FocusNFeService.emitir().
Formato baseado na API v2 Focus NFe (https://doc.focusnfe.com.br/reference/emitir_nfce.md).

Campos-chave da v2 NFC-e:
  - cnpj_emitente  → topo (não aninhado em emitente:{})
  - formas_pagamento → (não pagamentos!)
  - local_destino   → obrigatório
  - destinatário    → campos individuais no topo

Regra GTIN (SEFAZ NT 2011/004):
  - Produto COM código de barras  → codigo_ean / codigo_ean_tributavel = EAN
  - Produto SEM código de barras  → codigo_ean / codigo_ean_tributavel = "SEM GTIN"
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError

_BRT = ZoneInfo("America/Sao_Paulo")


def _data_emissao_brt(dt=None) -> str:
    """
    Converte dt (UTC) para BRT (America/Sao_Paulo) e retorna ISO 8601 com offset.
    Ex.: "2026-07-14T10:49:10-03:00"

    Critério SEFAZ: dhEmi deve refletir o fuso local da NFC-e,
    nunca UTC com offset -03:00 colado na mão (causaria rejeição 703).
    """
    if dt is None:
        dt = timezone.now()
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt.astimezone(_BRT).isoformat(timespec="seconds")
from apps.financeiro.models import DocumentoFiscal
from apps.financeiro.constants.enums import TipoDocumentoFiscal, StatusDocumentoFiscal
from apps.pdv.models import VendaPDV


# ---------------------------------------------------------------------------
# Mapeamento: tipo de pagamento PDV (FormaPagamento.tipo) → código FocusNFe
# Referência: Tabela D - Meio de Pagamento (SEFAZ)
# ---------------------------------------------------------------------------
_FORMA_PGTO_FOCO = {
    "dinheiro":       "01",
    "cheque":         "02",
    "cartao_credito": "03",
    "cartao_debito":  "04",
    "vale":           "10",   # vale alimentação/refeição
    "convenio":       "05",   # crédito loja / convênio
    "crediario":      "05",
    "pix":            "17",
    "boleto":         "15",
    "ted":            "16",
    "doc":            "16",
}


def _codigo_ean(produto) -> str:
    """
    Retorna o GTIN do produto ou 'SEM GTIN' quando não cadastrado.
    Exigência SEFAZ — campos cEAN e cEANTrib do XML NF-e.
    """
    ean = (produto.codigo_barras or "").strip()
    # Aceita apenas EAN-8, EAN-13 e DUN-14 (8, 12, 13 ou 14 dígitos numéricos)
    if ean and ean.isdigit() and len(ean) in (8, 12, 13, 14):
        return ean
    return "SEM GTIN"


def _cfop_item(produto, filial) -> str:
    """Resolve CFOP de venda. Prioriza dado do produto; fallback para 5102/5405."""
    cfop = produto.cfop_venda_interna or ""
    if not cfop:
        cfop = "5102"
    return cfop


def _regime_tributario_cod(filial) -> int:
    """
    Retorna o código do regime tributário da filial.
    1=Simples Nacional, 2=SN excesso, 3=Normal/Lucro Real/Presumido
    """
    cod = getattr(filial, "codigo_regime_tributario", None)
    if cod:
        return int(cod)
    empresa_cod = getattr(getattr(filial, "empresa", None), "codigo_regime_tributario", None)
    if empresa_cod:
        return int(empresa_cod)
    return 1  # default: Simples Nacional


def _montar_item(numero: int, item_venda, filial) -> dict:
    """
    Monta o dicionário de um item no payload FocusNFe (formato v2, campos flat).

    ICMS Simples Nacional: icms_csosn + icms_origem (flat, sem objeto aninhado)
    ICMS Normal:           icms_cst + icms_origem + campos de base/alíquota/valor
    PIS/COFINS:            campos flat icms_situacao_tributaria / cofins_*
    """
    produto = item_venda.produto
    quantidade = float(item_venda.quantidade)
    valor_unitario = float(item_venda.valor_unitario)
    valor_bruto = float(item_venda.quantidade * item_venda.valor_unitario)
    valor_total = float(item_venda.valor_total)
    unidade = item_venda.unidade_medida or (
        produto.unidade_medida.sigla if produto.unidade_medida_id else "UN"
    )
    descricao = (produto.descricao_pdv or produto.descricao or "")[:120]
    ncm = (produto.ncm or "").replace(".", "").strip()
    cfop = _cfop_item(produto, filial)
    ean = _codigo_ean(produto)

    item: Dict[str, Any] = {
        "numero_item": numero,
        "codigo_produto": produto.codigo or str(produto.pk),
        "descricao": descricao,
        "codigo_ncm": ncm,
        "cfop": cfop,
        "unidade_comercial": unidade,
        "quantidade_comercial": quantidade,
        "valor_unitario_comercial": valor_unitario,
        "valor_bruto": valor_bruto,
        "valor_total": valor_total,
        # ─── GTIN (cEAN / cEANTrib) ─────────────────────────────────────────
        "codigo_ean": ean,
        "codigo_ean_tributavel": ean,
        # ────────────────────────────────────────────────────────────────────
        "unidade_tributavel": unidade,
        "quantidade_tributavel": quantidade,
        "valor_unitario_tributavel": valor_unitario,
        "inclui_no_total": "1",
    }

    origem = str(int(getattr(produto, "origem_produto", 0) or 0))

    regime = _regime_tributario_cod(filial)
    if regime == 1:
        # Simples Nacional — campos flat
        csosn = (getattr(produto, "cst_csosn", "") or "").strip() or "400"
        item["icms_csosn"] = csosn
        item["icms_origem"] = origem
    else:
        # Regime Normal — campos flat
        cst = (getattr(produto, "cst_csosn", "") or "00").strip()
        item["icms_cst"] = cst
        item["icms_origem"] = origem
        item["icms_modalidade_base_calculo"] = "3"
        item["icms_base_calculo"] = valor_total
        item["icms_aliquota"] = float(getattr(produto, "aliquota_icms", 0) or 0)
        item["icms_valor"] = 0.0

    # PIS / COFINS (flat)
    cst_pis = (getattr(produto, "cst_pis", "") or "07").strip() or "07"
    item["pis_situacao_tributaria"] = cst_pis
    item["pis_base_calculo"] = 0.0
    item["pis_aliquota_percentual"] = 0.0
    item["pis_valor"] = 0.0

    cst_cofins = (getattr(produto, "cst_cofins", "") or "07").strip() or "07"
    item["cofins_situacao_tributaria"] = cst_cofins
    item["cofins_base_calculo"] = 0.0
    item["cofins_aliquota_percentual"] = 0.0
    item["cofins_valor"] = 0.0

    # CEST — campo opcional
    cest = (getattr(produto, "cest", "") or "").strip()
    if cest:
        item["codigo_cest"] = cest

    return item


def _montar_formas_pagamento(pagamentos_qs) -> list:
    """Converte os pagamentos da venda no formato FocusNFe v2 (array formas_pagamento)."""
    pgtos = []
    for pgto in pagamentos_qs:
        tipo = (pgto.forma_pagamento.tipo or "").lower().strip()
        codigo = _FORMA_PGTO_FOCO.get(tipo, "99")
        pgtos.append({
            "forma_pagamento": codigo,
            "valor_pagamento": float(pgto.valor),
        })
    return pgtos or [{"forma_pagamento": "99", "valor_pagamento": 0.0}]


class NfcePayloadBuilder:
    """
    Constrói o payload JSON de NFC-e (Nota Fiscal de Consumidor Eletrônica)
    para envio via Focus NFe v2 a partir de uma VendaPDV finalizada.

    Formato v2:
    - cnpj_emitente no topo (não aninhado)
    - formas_pagamento (não pagamentos)
    - local_destino obrigatório
    - destinatario: campos no topo (nome_destinatario, cpf_destinatario, cnpj_destinatario)
    - itens: campos ICMS/PIS/COFINS flat (sem objetos aninhados)
    """

    @classmethod
    def build(cls, venda: VendaPDV) -> Dict[str, Any]:
        filial = venda.filial
        cliente = venda.cliente

        itens_qs = list(
            venda.itens.select_related("produto__unidade_medida").order_by("numero_item")
        )
        pagamentos_qs = list(
            venda.pagamentos.select_related("forma_pagamento").order_by("id")
        )

        if not itens_qs:
            raise DadosInvalidosError("Venda sem itens — não é possível emitir NFC-e.")

        data_emissao = _data_emissao_brt(venda.data_venda)

        items = [
            _montar_item(idx + 1, item, filial)
            for idx, item in enumerate(itens_qs)
        ]

        cnpj = (filial.cnpj or "").replace(".", "").replace("/", "").replace("-", "")

        payload: Dict[str, Any] = {
            # ── Identificação do emitente (topo, formato v2) ────────────────
            "cnpj_emitente": cnpj,
            # ── Dados da nota ───────────────────────────────────────────────
            "natureza_operacao": "VENDA AO CONSUMIDOR",
            "numero": venda.numero_venda,
            "serie": str(filial.serie_nfce or 1),
            "data_emissao": data_emissao,
            # ── Campos obrigatórios NFC-e v2 ────────────────────────────────
            "local_destino": "1",         # 1=operação interna (sempre para PDV)
            "presenca_comprador": "1",    # 1=presencial
            "modalidade_frete": "9",      # 9=sem frete
            # ── Itens e pagamentos ──────────────────────────────────────────
            "items": items,
            "formas_pagamento": _montar_formas_pagamento(pagamentos_qs),
            # ── Totais ──────────────────────────────────────────────────────
            "valor_produtos": float(venda.valor_subtotal or 0),
            "valor_desconto": float(venda.valor_desconto or 0),
            "valor_total": float(venda.valor_total),
        }

        # Destinatário: campos no topo (formato v2)
        if cliente:
            cpf_cnpj = (cliente.cpf_cnpj or "").replace(".", "").replace("-", "").replace("/", "").strip()
            if cpf_cnpj:
                payload["nome_destinatario"] = cliente.razao_social or "Consumidor Final"
                if len(cpf_cnpj) == 11:
                    payload["cpf_destinatario"] = cpf_cnpj
                elif len(cpf_cnpj) == 14:
                    payload["cnpj_destinatario"] = cpf_cnpj

        return payload


class NfePayloadBuilder:
    """
    Constrói o payload JSON de NF-e (Nota Fiscal Eletrônica)
    para envio via Focus NFe a partir de uma VendaPDV finalizada.
    """

    @classmethod
    def build(cls, venda: VendaPDV, numero_nfe: int, serie_nfe: int = 1) -> Dict[str, Any]:
        filial = venda.filial
        cliente = venda.cliente

        itens_qs = list(
            venda.itens.select_related("produto__unidade_medida").order_by("numero_item")
        )
        pagamentos_qs = list(
            venda.pagamentos.select_related("forma_pagamento").order_by("id")
        )

        if not itens_qs:
            raise DadosInvalidosError("Venda sem itens — não é possível emitir NF-e.")

        data_emissao = _data_emissao_brt(venda.data_venda)

        items = [
            _montar_item(idx + 1, item, filial)
            for idx, item in enumerate(itens_qs)
        ]

        cnpj = (filial.cnpj or "").replace(".", "").replace("/", "").replace("-", "")

        payload: Dict[str, Any] = {
            "cnpj_emitente": cnpj,
            "natureza_operacao": "VENDA DE MERCADORIAS",
            "numero": numero_nfe,
            "serie": str(serie_nfe),
            "data_emissao": data_emissao,
            "data_entrada_saida": data_emissao,
            "tipo_documento": "1",
            "finalidade_emissao": "1",
            "consumidor_final": "1",
            "presenca_comprador": "1",
            "local_destino": "1",
            "modalidade_frete": "9",
            "items": items,
            "formas_pagamento": _montar_formas_pagamento(pagamentos_qs),
            "valor_produtos": float(venda.valor_subtotal or 0),
            "valor_desconto": float(venda.valor_desconto or 0),
            "valor_total": float(venda.valor_total),
        }

        if cliente:
            cpf_cnpj = (cliente.cpf_cnpj or "").replace(".", "").replace("-", "").replace("/", "").strip()
            if cpf_cnpj:
                payload["nome_destinatario"] = cliente.razao_social or "Consumidor Final"
                if len(cpf_cnpj) == 11:
                    payload["cpf_destinatario"] = cpf_cnpj
                elif len(cpf_cnpj) == 14:
                    payload["cnpj_destinatario"] = cpf_cnpj

        return payload


@transaction.atomic
def emitir_nfce_para_venda(venda: VendaPDV, usuario) -> DocumentoFiscal:
    """
    Wrapper de alto nível: constrói payload NFC-e, cria DocumentoFiscal e dispara emissão.
    Retorna o DocumentoFiscal criado/atualizado.

    NFC-e é SÍNCRONA na Focus NFe: o retorno já tem o status final (autorizado/erro).
    """
    from apps.fiscal.services.focusnfe_service import FocusNFeService
    from apps.fiscal.integrations.focusnfe import FocusNFeClient
    from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig

    filial = venda.filial

    # Verifica se já existe documento fiscal para esta venda
    existente = DocumentoFiscal.objects.filter(
        origem_tipo="venda_pdv",
        origem_id=venda.pk,
        tipo_documento="nfce",
    ).exclude(status=StatusDocumentoFiscal.CANCELADA).first()
    if existente and existente.status == StatusDocumentoFiscal.AUTORIZADA:
        return existente

    payload = NfcePayloadBuilder.build(venda)

    doc = DocumentoFiscal.objects.create(
        filial=filial,
        tipo_documento="nfce",
        origem_tipo="venda_pdv",
        origem_id=venda.pk,
        numero=venda.numero_venda,
        serie=filial.serie_nfce or 1,
        emitente_cnpj=filial.cnpj,
        destinatario_tipo="cliente" if venda.cliente_id else "consumidor",
        destinatario_id=venda.cliente_id,
        destinatario_snapshot=(
            {
                "nome": venda.cliente.razao_social,
                "cpf_cnpj": venda.cliente.cpf_cnpj or "",
            }
            if venda.cliente else {"nome": "Consumidor Final"}
        ),
        valor_produtos=venda.valor_subtotal or 0,
        valor_desconto=venda.valor_desconto or 0,
        valor_total=venda.valor_total,
        status=StatusDocumentoFiscal.PENDENTE,
        data_emissao=venda.data_venda or timezone.now(),
        usuario=usuario,
    )

    # Usa token da filial se configurado, senão usa o global (env var)
    filial_token = getattr(filial, "focusnfe_token", "") or ""
    filial_ambiente = getattr(filial, "focusnfe_ambiente", None)
    if filial_token:
        config = FocusNFeConfig.from_env(token=filial_token, ambiente=filial_ambiente)
        client = FocusNFeClient(config=config)
        service = FocusNFeService(client=client)
    else:
        service = FocusNFeService()

    return service.emitir(doc, payload)
