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
  - Produto COM código de barras  → codigo_barras_comercial / tributavel = EAN
  - Produto SEM código de barras  → campos de barras = "SEM GTIN"
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import secrets
from typing import Any, Dict, Optional

from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.fiscal.services.ibpt_service import obter_aliquota_ibpt

_BRT = ZoneInfo("America/Sao_Paulo")
_MONEY = Decimal("0.01")


def _decimal(valor: Any) -> Decimal:
    return Decimal(str(valor or 0))


def _dinheiro(valor: Any) -> Decimal:
    return _decimal(valor).quantize(_MONEY, rounding=ROUND_HALF_UP)


def _percentual(base: Decimal, aliquota: Any) -> Decimal:
    return _dinheiro(base * _decimal(aliquota) / Decimal("100"))


def _float_dinheiro(valor: Any) -> float:
    return float(_dinheiro(valor))


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


def _momento_emissao(venda):
    """Mantem a hora da venda quando recente e evita rejeicao ao emitir pelo historico."""
    agora = timezone.now()
    data_venda = getattr(venda, "data_venda", None)
    if data_venda and abs(agora - data_venda) <= timedelta(minutes=4):
        return data_venda
    return agora
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

_BANDEIRA_CARTAO_FOCUS = {
    "visa": "01", "mastercard": "02", "master": "02", "american express": "03",
    "amex": "03", "sorocred": "04", "diners": "05", "elo": "06",
    "hipercard": "07", "aura": "08", "cabal": "09", "hiper": "17",
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


def _somente_digitos(valor: Any) -> str:
    return "".join(ch for ch in str(valor or "") if ch.isdigit())


def _regime_tributario_cod(filial) -> int:
    """
    Retorna o código do regime tributário da filial.
    1=Simples Nacional, 2=SN excesso, 3=Normal/Lucro Real/Presumido,
    4=Simples Nacional - MEI.
    """
    cod = getattr(filial, "codigo_regime_tributario", None)
    if cod:
        return int(cod)
    empresa_cod = getattr(getattr(filial, "empresa", None), "codigo_regime_tributario", None)
    if empresa_cod:
        return int(empresa_cod)
    return 1  # default: Simples Nacional


def _cfop_item_destino(produto, local_destino: str) -> str:
    if local_destino == "3":
        return (produto.cfop_venda_exportacao or "").strip() or "7102"
    if local_destino == "2":
        return (produto.cfop_venda_interestadual or "").strip() or "6102"
    return (produto.cfop_venda_interna or "").strip() or "5102"


def _base_reduzida(base: Decimal, reducao: Any) -> Decimal:
    percentual = min(max(_decimal(reducao), Decimal("0")), Decimal("100"))
    return _dinheiro(base * (Decimal("1") - percentual / Decimal("100")))


def _aplicar_icms(item: dict, produto, regime: int, base: Decimal) -> None:
    origem = str(int(getattr(produto, "origem_produto", 0) or 0))
    situacao = (getattr(produto, "cst_csosn", "") or ("400" if regime == 1 else "00")).strip()
    aliquota = _decimal(getattr(produto, "aliquota_icms", 0))
    reducao = _decimal(getattr(produto, "reducao_bc_icms", 0))
    item["icms_situacao_tributaria"] = situacao
    item["icms_origem"] = origem

    beneficio = (getattr(produto, "codigo_beneficio_fiscal_icms", "") or "").strip()
    if beneficio:
        item["codigo_beneficio_fiscal"] = beneficio

    if regime in {1, 4}:
        if situacao in {"101", "201"} and aliquota > 0:
            item["icms_aliquota_credito_simples"] = float(aliquota)
            item["icms_valor_credito_simples"] = _float_dinheiro(_percentual(base, aliquota))
        elif situacao == "900" and aliquota > 0:
            base_icms = _base_reduzida(base, reducao)
            item["icms_modalidade_base_calculo"] = getattr(produto, "modalidade_bc_icms", "") or "3"
            if reducao > 0:
                item["icms_reducao_base_calculo"] = float(reducao)
            item["icms_base_calculo"] = _float_dinheiro(base_icms)
            item["icms_aliquota"] = float(aliquota)
            item["icms_valor"] = _float_dinheiro(_percentual(base_icms, aliquota))
    elif situacao in {"00", "10", "20", "51", "70", "90"}:
        base_icms = _base_reduzida(base, reducao)
        item["icms_modalidade_base_calculo"] = getattr(produto, "modalidade_bc_icms", "") or "3"
        if reducao > 0:
            item["icms_reducao_base_calculo"] = float(reducao)
        item["icms_base_calculo"] = _float_dinheiro(base_icms)
        item["icms_aliquota"] = float(aliquota)
        item["icms_valor"] = _float_dinheiro(_percentual(base_icms, aliquota))

    aliquota_fcp = _decimal(getattr(produto, "aliquota_fcp", 0))
    if aliquota_fcp > 0 and situacao not in {"30", "40", "41", "50", "102", "103", "300", "400"}:
        base_fcp = _base_reduzida(base, reducao)
        item["fcp_base_calculo"] = _float_dinheiro(base_fcp)
        item["fcp_percentual"] = float(aliquota_fcp)
        item["fcp_valor"] = _float_dinheiro(_percentual(base_fcp, aliquota_fcp))

    aliquota_st = _decimal(getattr(produto, "aliquota_icms_st", 0))
    if situacao in {"10", "30", "70", "90", "201", "202", "203", "900"} and aliquota_st > 0:
        mva = _decimal(getattr(produto, "mva_icms_st", 0))
        base_st = _dinheiro(base * (Decimal("1") + mva / Decimal("100")))
        reducao_st = _decimal(getattr(produto, "reducao_bc_icms_st", 0))
        base_st = _base_reduzida(base_st, reducao_st)
        item["icms_modalidade_base_calculo_st"] = getattr(produto, "modalidade_bc_icms_st", "") or "4"
        if mva > 0:
            item["icms_margem_valor_adicionado_st"] = float(mva)
        if reducao_st > 0:
            item["icms_reducao_base_calculo_st"] = float(reducao_st)
        item["icms_base_calculo_st"] = _float_dinheiro(base_st)
        item["icms_aliquota_st"] = float(aliquota_st)
        icms_proprio = _decimal(item.get("icms_valor", 0)) if regime != 1 else Decimal("0")
        item["icms_valor_st"] = _float_dinheiro(
            max(_percentual(base_st, aliquota_st) - icms_proprio, Decimal("0"))
        )
        aliquota_fcp_st = _decimal(getattr(produto, "aliquota_fcp_st", 0))
        if aliquota_fcp_st > 0:
            item["fcp_base_calculo_st"] = _float_dinheiro(base_st)
            item["fcp_percentual_st"] = float(aliquota_fcp_st)
            item["fcp_valor_st"] = _float_dinheiro(_percentual(base_st, aliquota_fcp_st))


def _aplicar_pis_cofins(item: dict, produto, base: Decimal) -> None:
    nao_tributados = {"04", "05", "06", "07", "08", "09"}
    for prefixo, cst_nome, aliquota_nome in (
        ("pis", "cst_pis", "aliquota_pis"),
        ("cofins", "cst_cofins", "aliquota_cofins"),
    ):
        cst = (getattr(produto, cst_nome, "") or "07").strip() or "07"
        aliquota = _decimal(getattr(produto, aliquota_nome, 0))
        item[f"{prefixo}_situacao_tributaria"] = cst
        if cst in nao_tributados:
            # A Focus precisa de um valor fiscal para materializar PISNT/COFINSNT;
            # somente o CST pode resultar em grupos XML vazios (<PIS/>/<COFINS/>).
            item[f"{prefixo}_base_calculo"] = 0.0
            item[f"{prefixo}_valor"] = 0.0
            continue
        item[f"{prefixo}_base_calculo"] = _float_dinheiro(base)
        item[f"{prefixo}_aliquota_porcentual"] = float(aliquota)
        item[f"{prefixo}_valor"] = _float_dinheiro(_percentual(base, aliquota))


def _aplicar_ipi(item: dict, produto, base: Decimal) -> None:
    cst = (getattr(produto, "cst_ipi", "") or "").strip()
    if not cst:
        return
    aliquota = _decimal(getattr(produto, "aliquota_ipi", 0))
    item["ipi_situacao_tributaria"] = cst
    item["ipi_codigo_enquadramento_legal"] = getattr(produto, "codigo_enquadramento_ipi", "") or "999"
    if cst in {"00", "49", "50", "99"}:
        item["ipi_base_calculo"] = _float_dinheiro(base)
        item["ipi_aliquota"] = float(aliquota)
        item["ipi_valor"] = _float_dinheiro(_percentual(base, aliquota))


def _aplicar_ibpt(
    item: dict, produto, filial, base: Decimal, data_emissao: date
) -> None:
    # IBPT (Lei 12.741/2012 — valor aproximado dos tributos) e apenas
    # informativo: NAO e exigido pela SEFAZ para autorizar a nota. Se a
    # tabela nao estiver disponivel para o NCM (falha de rede, NCM sem
    # dados etc.), a nota deve seguir sem esse campo em vez de travar
    # a emissao inteira.
    try:
        aliquota = obter_aliquota_ibpt(
            getattr(produto, 'ncm', ''), getattr(filial, 'uf', ''), data_emissao
        )
    except DadosInvalidosError:
        return
    if aliquota is None:
        return

    origem = int(getattr(produto, 'origem_produto', 0) or 0)
    federal = (
        aliquota.federal_importado
        if origem in {1, 2, 6, 7}
        else aliquota.federal_nacional
    )
    # A tabela separa as cargas por esfera. Arredondar cada componente antes
    # da soma reproduz o valor apresentado ao consumidor pela metodologia IBPT.
    valor = (
        _percentual(base, federal)
        + _percentual(base, aliquota.estadual)
        + _percentual(base, aliquota.municipal)
    )
    item['valor_total_tributos'] = _float_dinheiro(valor)
    item['_ibpt_fonte'] = f'{aliquota.fonte} {aliquota.versao}'.strip()


def _aplicar_reforma_tributaria(
    item: dict, produto, base: Decimal, ano_emissao: int
) -> None:
    cst_cbs = (getattr(produto, "cst_cbs", "") or "").strip()
    cst_ibs = (getattr(produto, "cst_ibs", "") or "").strip()
    classe_cbs = (getattr(produto, "classificacao_tributaria_cbs", "") or "").strip()
    classe_ibs = (getattr(produto, "classificacao_tributaria_ibs", "") or "").strip()
    if not any((cst_cbs, cst_ibs, classe_cbs, classe_ibs)):
        return
    if not all((cst_cbs, cst_ibs, classe_cbs, classe_ibs)):
        raise DadosInvalidosError("Informe CST e classificacao tributaria de IBS e CBS no produto.")
    if cst_cbs != cst_ibs or classe_cbs != classe_ibs:
        raise DadosInvalidosError("A Focus exige um unico CST e uma unica classificacao para o grupo IBS/CBS.")

    item["ibs_cbs_situacao_tributaria"] = cst_ibs
    item["ibs_cbs_classificacao_tributaria"] = classe_ibs
    item["ibs_cbs_base_calculo"] = _float_dinheiro(base)
    reducao_ibs = _decimal(getattr(produto, "reducao_ibs", 0))
    if ano_emissao == 2026:
        aliq_uf = Decimal("0.1")
        aliq_mun = Decimal("0")
    else:
        aliq_uf = _decimal(getattr(produto, "aliquota_ibs_uf", 0))
        aliq_mun = _decimal(getattr(produto, "aliquota_ibs_municipal", 0))
    efetiva_uf = aliq_uf * (Decimal("1") - reducao_ibs / Decimal("100"))
    efetiva_mun = aliq_mun * (Decimal("1") - reducao_ibs / Decimal("100"))
    item["ibs_uf_aliquota"] = float(aliq_uf)
    item["ibs_mun_aliquota"] = float(aliq_mun)
    if reducao_ibs > 0:
        item["ibs_uf_percentual_reducao_aliquota"] = float(reducao_ibs)
        item["ibs_uf_aliquota_efetiva"] = float(efetiva_uf)
        item["ibs_mun_percentual_reducao_aliquota"] = float(reducao_ibs)
        item["ibs_mun_aliquota_efetiva"] = float(efetiva_mun)
    valor_ibs_uf = _percentual(base, efetiva_uf)
    valor_ibs_mun = _percentual(base, efetiva_mun)
    item["ibs_uf_valor"] = _float_dinheiro(valor_ibs_uf)
    item["ibs_mun_valor"] = _float_dinheiro(valor_ibs_mun)
    item["ibs_valor_total"] = _float_dinheiro(valor_ibs_uf + valor_ibs_mun)

    reducao_cbs = _decimal(getattr(produto, "reducao_cbs", 0))
    aliq_cbs = (
        Decimal("0.9")
        if ano_emissao == 2026
        else _decimal(getattr(produto, "aliquota_cbs", 0))
    )
    efetiva_cbs = aliq_cbs * (Decimal("1") - reducao_cbs / Decimal("100"))
    item["cbs_aliquota"] = float(aliq_cbs)
    if reducao_cbs > 0:
        item["cbs_percentual_reducao_aliquota"] = float(reducao_cbs)
        item["cbs_aliquota_efetiva"] = float(efetiva_cbs)
    item["cbs_valor"] = _float_dinheiro(_percentual(base, efetiva_cbs))

    cst_is = (getattr(produto, "cst_is", "") or "").strip()
    classe_is = (getattr(produto, "classificacao_tributaria_is", "") or "").strip()
    if cst_is or classe_is:
        if not cst_is or not classe_is:
            raise DadosInvalidosError("Informe CST e classificacao tributaria do Imposto Seletivo.")
        aliq_is = _decimal(getattr(produto, "aliquota_is", 0))
        # O grupo IS é opcional. A Focus omite pIS/vIS quando a alíquota é zero,
        # produzindo um XML inválido; nesse caso o grupo não deve ser enviado.
        if aliq_is > 0:
            item["is_situacao_tributaria"] = cst_is
            item["is_classificacao_tributaria"] = classe_is
            item["is_base_calculo"] = _float_dinheiro(base)
            item["is_aliquota"] = float(aliq_is)
            item["is_valor"] = _float_dinheiro(_percentual(base, aliq_is))


def _montar_item_fiscal(
    numero: int,
    item_venda,
    filial,
    local_destino: str,
    data_emissao: date,
    desconto_rateado: Decimal,
    acrescimo_rateado: Decimal,
) -> dict:
    produto = item_venda.produto
    quantidade = _decimal(item_venda.quantidade)
    valor_unitario = _decimal(item_venda.valor_unitario)
    valor_bruto = _dinheiro(quantidade * valor_unitario)
    desconto = _dinheiro(_decimal(item_venda.desconto_valor) + desconto_rateado)
    acrescimo = _dinheiro(_decimal(item_venda.acrescimo_valor) + acrescimo_rateado)
    base = _dinheiro(valor_bruto - desconto + acrescimo)
    unidade = item_venda.unidade_medida or (
        produto.unidade_medida.sigla if produto.unidade_medida_id else "UN"
    )
    ean = _codigo_ean(produto)
    item: Dict[str, Any] = {
        "numero_item": numero,
        "codigo_produto": produto.codigo or str(produto.pk),
        "descricao": (produto.descricao_pdv or produto.descricao or "")[:120],
        "codigo_ncm": (produto.ncm or "").replace(".", "").strip(),
        "cfop": _cfop_item_destino(produto, local_destino),
        "unidade_comercial": unidade,
        "quantidade_comercial": float(quantidade),
        "valor_unitario_comercial": float(valor_unitario),
        "valor_bruto": _float_dinheiro(valor_bruto),
        "valor_total": _float_dinheiro(base),
        "codigo_barras_comercial": ean,
        "codigo_barras_tributavel": ean,
        "unidade_tributavel": unidade,
        "quantidade_tributavel": float(quantidade),
        "valor_unitario_tributavel": float(valor_unitario),
        "inclui_no_total": "1",
    }
    if desconto > 0:
        item["valor_desconto"] = _float_dinheiro(desconto)
    if acrescimo > 0:
        item["valor_outras_despesas"] = _float_dinheiro(acrescimo)
    cest = (getattr(produto, "cest", "") or "").strip()
    if cest:
        item["cest"] = cest
    ex_tipi = (getattr(produto, "ex_tipi", "") or "").strip()
    if ex_tipi:
        item["codigo_ex_tipi"] = ex_tipi
    _aplicar_icms(item, produto, _regime_tributario_cod(filial), base)
    _aplicar_pis_cofins(item, produto, base)
    _aplicar_ipi(item, produto, base)
    _aplicar_reforma_tributaria(item, produto, base, data_emissao.year)
    _aplicar_ibpt(item, produto, filial, base, data_emissao)
    return item


def _ratear(valor: Any, itens: list) -> list[Decimal]:
    total = _dinheiro(valor)
    if total == 0 or not itens:
        return [Decimal("0") for _ in itens]
    bases = [_dinheiro(_decimal(i.quantidade) * _decimal(i.valor_unitario)) for i in itens]
    soma_bases = sum(bases, Decimal("0"))
    if soma_bases <= 0:
        raise DadosInvalidosError("Nao foi possivel ratear os totais fiscais entre os itens.")
    resultado = []
    acumulado = Decimal("0")
    for indice, base in enumerate(bases):
        parcela = total - acumulado if indice == len(bases) - 1 else _dinheiro(total * base / soma_bases)
        resultado.append(parcela)
        acumulado += parcela
    return resultado


def _montar_itens(venda, itens: list, local_destino: str) -> list[dict]:
    descontos_proprios = sum((_decimal(i.desconto_valor) for i in itens), Decimal("0"))
    acrescimos_proprios = sum((_decimal(i.acrescimo_valor) for i in itens), Decimal("0"))
    descontos = _ratear(max(_decimal(venda.valor_desconto) - descontos_proprios, Decimal("0")), itens)
    acrescimos = _ratear(max(_decimal(venda.valor_acrescimo) - acrescimos_proprios, Decimal("0")), itens)
    data_emissao = date.fromisoformat(_data_emissao_brt(_momento_emissao(venda))[:10])
    return [
        _montar_item_fiscal(
            i + 1,
            item,
            venda.filial,
            local_destino,
            data_emissao,
            descontos[i],
            acrescimos[i],
        )
        for i, item in enumerate(itens)
    ]


def _montar_formas_pagamento(pagamentos_qs) -> list:
    """Converte os pagamentos da venda no formato FocusNFe v2 (array formas_pagamento)."""
    pgtos = []
    for pgto in pagamentos_qs:
        tipo = (pgto.forma_pagamento.tipo or "").lower().strip()
        codigo = (pgto.forma_pagamento.codigo_sefaz or "").strip() or _FORMA_PGTO_FOCO.get(tipo, "99")
        forma = {
            "forma_pagamento": codigo,
            "valor_pagamento": float(pgto.valor),
        }
        if tipo in {"cartao_credito", "cartao_debito"}:
            tef = getattr(pgto, "tef_transacao", None)
            autorizacao = (pgto.autorizacao or getattr(tef, "autorizacao", "") or "").strip()
            bandeira = (pgto.bandeira or getattr(tef, "bandeira", "") or "").strip().lower()
            # O ERP ainda nao armazena o CNPJ da credenciadora por transacao.
            # Sem esse dado, a declaracao fiscal correta e POS nao integrado.
            forma["tipo_integracao"] = "2"
            if autorizacao:
                forma["numero_autorizacao"] = autorizacao[:20]
            if bandeira:
                forma["bandeira_operadora"] = _BANDEIRA_CARTAO_FOCUS.get(bandeira, "99")
        pgtos.append(forma)
    return pgtos or [{"forma_pagamento": "99", "valor_pagamento": 0.0}]


def _validar_configuracao_nfce(filial, params) -> None:
    token = (getattr(filial, "focusnfe_token", "") or "").strip()
    if not token:
        raise DadosInvalidosError("Configure o Token Focus NFe da filial antes de emitir NFC-e.")

    csc_id = (getattr(params, "nfce_csc_id", "") or "").strip()
    csc_token = (getattr(params, "nfce_csc_token", "") or "").strip()
    if not csc_id or not csc_token:
        ambiente = int(getattr(filial, "focusnfe_ambiente", 2) or 2)
        nome_ambiente = "producao" if ambiente == 1 else "homologacao"
        raise DadosInvalidosError(
            "Configure o CSC ID e o CSC Token NFC-e nos Parametros do Sistema "
            f"antes de emitir NFC-e em {nome_ambiente}."
        )


def _endereco_preferencial_cliente(cliente) -> dict:
    """Retorna o endereco fiscal preferencial do cliente, sem inventar dados."""
    endereco = {
        "logradouro": getattr(cliente, "endereco", "") or "",
        "numero": getattr(cliente, "numero", "") or "",
        "complemento": getattr(cliente, "complemento", "") or "",
        "bairro": getattr(cliente, "bairro", "") or "",
        "municipio": getattr(cliente, "cidade", "") or "",
        "uf": getattr(cliente, "uf", "") or "",
        "cep": getattr(cliente, "cep", "") or "",
        "codigo_municipio": getattr(cliente, "codigo_municipio_ibge", "") or "",
        "pais": getattr(cliente, "pais", "") or "Brasil",
        "codigo_pais": getattr(cliente, "codigo_pais_bacen", "") or "1058",
    }
    try:
        extra = cliente.enderecos.filter(ativo=True).order_by("-padrao", "id").first()
    except Exception:
        extra = None
    if extra:
        endereco.update({
            "logradouro": extra.endereco or endereco["logradouro"],
            "numero": extra.numero or endereco["numero"],
            "complemento": extra.complemento or endereco["complemento"],
            "bairro": extra.bairro or endereco["bairro"],
            "municipio": extra.cidade or endereco["municipio"],
            "uf": extra.uf or endereco["uf"],
            "cep": extra.cep or endereco["cep"],
            "codigo_municipio": extra.codigo_municipio_ibge or endereco["codigo_municipio"],
            "pais": extra.pais or endereco["pais"],
            "codigo_pais": extra.codigo_pais_bacen or endereco["codigo_pais"],
        })
    return endereco


def _local_destino(filial, cliente) -> str:
    if not cliente:
        return "1"
    endereco = _endereco_preferencial_cliente(cliente)
    codigo_pais = _somente_digitos(endereco.get("codigo_pais")) or "1058"
    pais = (endereco.get("pais") or "Brasil").strip().lower()
    if codigo_pais != "1058" or pais not in {"brasil", "brazil"}:
        return "3"
    uf_destino = (endereco.get("uf") or "").strip().upper()
    uf_emitente = (getattr(filial, "uf", "") or "").strip().upper()
    return "2" if uf_destino and uf_emitente and uf_destino != uf_emitente else "1"


def _indicador_consumidor_final(cliente) -> int:
    """CPF e sempre consumidor final; para CNPJ respeita o cadastro fiscal."""
    cpf_cnpj = _somente_digitos(getattr(cliente, "cpf_cnpj", ""))
    if len(cpf_cnpj) == 11:
        return 1
    return 1 if getattr(cliente, "consumidor_final", True) else 0


def _validar_regras_mei(filial, items: list[dict], modelo: int, local_destino: str) -> None:
    """Antecipa as validacoes N12a-80/81/90/91 da NT 2024.001."""
    if _regime_tributario_cod(filial) != 4 or local_destino == "3":
        return

    csosn_permitidos = {"102", "300"} if modelo == 65 else {"102", "300", "400", "900"}
    cfops_csosn_900 = {
        "1202", "1904", "2202", "2904", "5202", "5904", "6202", "6904",
        "1501", "1503", "1504", "1505", "1506", "1553", "2501", "2503",
        "2504", "2505", "2506", "2553", "5501", "5502", "5504", "5505",
        "5551", "5933", "6501", "6502", "6504", "6505", "6551", "6933",
    }
    for item in items:
        numero = item.get("numero_item")
        csosn = str(item.get("icms_situacao_tributaria") or "")
        cfop = str(item.get("cfop") or "")
        if csosn not in csosn_permitidos:
            permitidos = ", ".join(sorted(csosn_permitidos))
            raise DadosInvalidosError(
                f"Item {numero}: emitente MEI (CRT 4) aceita CSOSN {permitidos} "
                f"na NF-{'C' if modelo == 65 else ''}e."
            )
        if modelo == 65 and cfop != "5102":
            raise DadosInvalidosError(
                f"Item {numero}: NFC-e de emitente MEI aceita somente CFOP 5102."
            )
        if modelo == 55 and csosn == "102" and cfop not in {"5102", "6102"}:
            raise DadosInvalidosError(
                f"Item {numero}: NF-e de emitente MEI com CSOSN 102 aceita CFOP 5102 ou 6102."
            )
        if modelo == 55 and csosn == "900" and cfop not in cfops_csosn_900:
            raise DadosInvalidosError(
                f"Item {numero}: CFOP {cfop} nao e permitido para emitente MEI com CSOSN 900."
            )


def _aplicar_totais_fiscais(payload: dict, venda, items: list[dict]) -> None:
    fontes_ibpt = {
        item.pop('_ibpt_fonte') for item in items if item.get('_ibpt_fonte')
    }
    payload["valor_produtos"] = _float_dinheiro(
        sum((_decimal(item["valor_bruto"]) for item in items), Decimal("0"))
    )
    payload["valor_desconto"] = _float_dinheiro(
        sum((_decimal(item.get("valor_desconto", 0)) for item in items), Decimal("0"))
    )
    payload["valor_outras_despesas"] = _float_dinheiro(
        sum((_decimal(item.get("valor_outras_despesas", 0)) for item in items), Decimal("0"))
    )
    valor_st = sum((_decimal(item.get("icms_valor_st", 0)) for item in items), Decimal("0"))
    valor_ipi = sum((_decimal(item.get("ipi_valor", 0)) for item in items), Decimal("0"))
    valor_total = _dinheiro(
        _decimal(payload["valor_produtos"])
        - _decimal(payload["valor_desconto"])
        + _decimal(payload["valor_outras_despesas"])
        + valor_st
        + valor_ipi
    )
    if valor_total != _dinheiro(venda.valor_total):
        raise DadosInvalidosError(
            "O total fiscal com IPI/ICMS-ST difere do total cobrado na venda. "
            "Revise a formacao do preco antes de emitir."
        )
    payload["valor_total"] = _float_dinheiro(valor_total)

    if any('valor_total_tributos' in item for item in items):
        payload['valor_total_tributos'] = _float_dinheiro(
            sum(
                (_decimal(item.get('valor_total_tributos', 0)) for item in items),
                Decimal('0'),
            )
        )
        payload['informacoes_adicionais_contribuinte'] = (
            'Valor aproximado dos tributos conforme Lei 12.741/2012. Fonte: '
            + ', '.join(sorted(fontes_ibpt))
        )[:5000]

    if any("ibs_cbs_situacao_tributaria" in item for item in items):
        for item in items:
            item["valor_total_item"] = _float_dinheiro(item["valor_total"])
        payload["ibs_cbs_is_valor_total"] = _float_dinheiro(
            sum((_decimal(item["valor_total_item"]) for item in items), Decimal("0"))
        )


def _aplicar_informacoes_adicionais(payload: dict, texto: str) -> None:
    """Preserva a mensagem IBPT e acrescenta a observacao informada pelo operador."""
    texto = " ".join(str(texto or "").split()).strip()
    existente = (payload.get("informacoes_adicionais_contribuinte") or "").strip()
    if texto and existente:
        texto = texto[:max(0, 5000 - len(existente) - 3)].rstrip()
    partes = [parte for parte in (texto, existente) if parte]
    if partes:
        payload["informacoes_adicionais_contribuinte"] = " | ".join(partes)[:5000]


def _aplicar_destinatario(
    payload: Dict[str, Any],
    cliente,
    *,
    exigir_endereco: bool = False,
    incluir_contato: bool = True,
) -> None:
    if not cliente:
        if exigir_endereco:
            raise DadosInvalidosError("Informe um cliente com CPF/CNPJ e endereco completo para emitir NF-e.")
        return

    cpf_cnpj = _somente_digitos(getattr(cliente, "cpf_cnpj", ""))
    if not cpf_cnpj:
        if not exigir_endereco:
            return
        raise DadosInvalidosError("Cliente sem CPF/CNPJ informado.")

    if len(cpf_cnpj) == 11:
        payload["nome_destinatario"] = getattr(cliente, "razao_social", "") or "Consumidor Final"
        payload["cpf_destinatario"] = cpf_cnpj
    elif len(cpf_cnpj) == 14:
        payload["nome_destinatario"] = getattr(cliente, "razao_social", "") or "Consumidor Final"
        payload["cnpj_destinatario"] = cpf_cnpj
    else:
        if not exigir_endereco:
            return
        raise DadosInvalidosError("CPF/CNPJ do cliente invalido para emissao fiscal.")

    ie = (getattr(cliente, "inscricao_estadual", "") or getattr(cliente, "rg_ie", "") or "").strip()
    if ie:
        payload["inscricao_estadual_destinatario"] = ie
    if getattr(cliente, "contribuinte_icms", False) and ie and ie.upper() != "ISENTO":
        payload["indicador_inscricao_estadual_destinatario"] = "1"
    elif ie.upper() == "ISENTO":
        payload["indicador_inscricao_estadual_destinatario"] = "2"
    else:
        payload["indicador_inscricao_estadual_destinatario"] = "9"

    if incluir_contato:
        telefone = _somente_digitos(getattr(cliente, "celular", "") or getattr(cliente, "telefone", ""))
        if telefone:
            payload["telefone_destinatario"] = telefone[:20]
        email = (getattr(cliente, "email_nfe", "") or getattr(cliente, "email", "") or "").strip()
        if email:
            payload["email_destinatario"] = email[:80]

    if not exigir_endereco:
        return

    endereco = _endereco_preferencial_cliente(cliente)
    faltando = []
    obrigatorios = {
        "logradouro": "logradouro",
        "numero": "numero",
        "bairro": "bairro",
        "municipio": "municipio",
        "uf": "UF",
    }
    for campo, label in obrigatorios.items():
        if not (endereco.get(campo) or "").strip():
            faltando.append(label)
    if faltando:
        raise DadosInvalidosError(
            "NF-e modelo 55 exige endereco completo do destinatario. "
            f"Complete no cadastro do cliente: {', '.join(faltando)}."
        )

    payload["logradouro_destinatario"] = endereco["logradouro"][:60]
    payload["numero_destinatario"] = (endereco["numero"] or "SN")[:60]
    if endereco["complemento"]:
        payload["complemento_destinatario"] = endereco["complemento"][:60]
    payload["bairro_destinatario"] = endereco["bairro"][:60]
    payload["municipio_destinatario"] = endereco["municipio"][:60]
    payload["uf_destinatario"] = endereco["uf"][:2].upper()
    cep = _somente_digitos(endereco["cep"])
    if cep:
        payload["cep_destinatario"] = cep[:8]
    codigo_municipio = _somente_digitos(endereco["codigo_municipio"])
    if len(codigo_municipio) == 7:
        payload["codigo_municipio_destinatario"] = codigo_municipio
    payload["pais_destinatario"] = endereco["pais"] or "Brasil"
    codigo_pais = _somente_digitos(endereco["codigo_pais"])
    if codigo_pais:
        payload["codigo_pais_destinatario"] = codigo_pais


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
    def build(
        cls, venda: VendaPDV, numero: int = None, serie: int = None,
        informacoes_adicionais: str = "",
    ) -> Dict[str, Any]:
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

        data_emissao = _data_emissao_brt(_momento_emissao(venda))

        # NFC-e do PDV representa venda presencial no estabelecimento.
        local_destino = "1"
        items = _montar_itens(venda, itens_qs, local_destino)

        cnpj = (filial.cnpj or "").replace(".", "").replace("/", "").replace("-", "")

        numero_nfce = numero if numero is not None else venda.numero_venda
        serie_nfce = serie if serie is not None else (filial.serie_nfce or 1)

        payload: Dict[str, Any] = {
            # ── Identificação do emitente (topo, formato v2) ────────────────
            "cnpj_emitente": cnpj,
            # ── Dados da nota ───────────────────────────────────────────────
            "natureza_operacao": "VENDA AO CONSUMIDOR",
            "numero": numero_nfce,
            "serie": str(serie_nfce),
            "data_emissao": data_emissao,
            # Consumidor final tambem habilita a transparencia tributaria.
            "consumidor_final": 1,
            # ── Campos obrigatórios NFC-e v2 ────────────────────────────────
            "local_destino": local_destino,
            "presenca_comprador": "4" if venda.delivery else "1",
            "modalidade_frete": "9",      # 9=sem frete
            # ── Itens e pagamentos ──────────────────────────────────────────
            "items": items,
            "formas_pagamento": _montar_formas_pagamento(pagamentos_qs),
        }

        valor_troco = sum((_decimal(pgto.troco) for pgto in pagamentos_qs), Decimal("0"))
        if valor_troco > 0:
            payload["valor_troco"] = _float_dinheiro(valor_troco)

        _aplicar_totais_fiscais(payload, venda, items)
        _aplicar_informacoes_adicionais(payload, informacoes_adicionais)

        _validar_regras_mei(filial, items, modelo=65, local_destino=local_destino)

        # Destinatario: campos no topo (formato v2)
        _aplicar_destinatario(payload, cliente, exigir_endereco=False, incluir_contato=False)

        return payload


class NfePayloadBuilder:
    """
    Constrói o payload JSON de NF-e (Nota Fiscal Eletrônica)
    para envio via Focus NFe a partir de uma VendaPDV finalizada.
    """

    @classmethod
    def build(
        cls, venda: VendaPDV, numero_nfe: int, serie_nfe: int = 1,
        informacoes_adicionais: str = "",
    ) -> Dict[str, Any]:
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

        data_emissao = _data_emissao_brt(_momento_emissao(venda))

        local_destino = _local_destino(filial, cliente)
        items = _montar_itens(venda, itens_qs, local_destino)

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
            "consumidor_final": _indicador_consumidor_final(cliente),
            "presenca_comprador": "4" if venda.delivery else "1",
            "local_destino": local_destino,
            "modalidade_frete": "9",
            "items": items,
            "formas_pagamento": _montar_formas_pagamento(pagamentos_qs),
        }

        valor_troco = sum((_decimal(pgto.troco) for pgto in pagamentos_qs), Decimal("0"))
        if valor_troco > 0:
            payload["valor_troco"] = _float_dinheiro(valor_troco)

        _aplicar_totais_fiscais(payload, venda, items)
        _aplicar_informacoes_adicionais(payload, informacoes_adicionais)

        _validar_regras_mei(filial, items, modelo=55, local_destino=local_destino)

        _aplicar_destinatario(payload, cliente, exigir_endereco=True)

        return payload


@transaction.atomic
def emitir_nfce_para_venda(
    venda: VendaPDV, usuario, *, contingencia: bool = False,
    informacoes_adicionais: str = "",
) -> DocumentoFiscal:
    """
    Wrapper de alto nível: constrói payload NFC-e, cria DocumentoFiscal e dispara emissão.
    Retorna o DocumentoFiscal criado/atualizado.

    Usa ParametroDocumentoFiscal para série e número (reserva atômica).
    NFC-e é SÍNCRONA na Focus NFe: o retorno já tem o status final (autorizado/erro).
    """
    from apps.fiscal.services.focusnfe_service import FocusNFeService
    from apps.fiscal.integrations.focusnfe import FocusNFeClient
    from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig
    from apps.core.models.parametros import ParametroDocumentoFiscal, ParametrosSistema

    filial = venda.filial

    # Verifica se já existe documento fiscal para esta venda
    existente = DocumentoFiscal.objects.filter(
        origem_tipo="venda_pdv",
        origem_id=venda.pk,
        tipo_documento="nfce",
    ).exclude(status=StatusDocumentoFiscal.CANCELADA).first()
    if existente and existente.status == StatusDocumentoFiscal.AUTORIZADA:
        return existente

    # Reserva número atômico via ParametroDocumentoFiscal
    params, _ = ParametrosSistema.objects.get_or_create(filial=filial)
    _validar_configuracao_nfce(filial, params)

    doc_params = (
        ParametroDocumentoFiscal.objects
        .select_for_update()
        .filter(parametros=params, tipo_documento="nfce")
        .first()
    )
    if doc_params:
        numero_nfce = doc_params.proximo_numero
        serie_nfce = doc_params.serie or 1
        doc_params.proximo_numero = numero_nfce + 1
        doc_params.save(update_fields=["proximo_numero"])
    else:
        # Fallback se ainda não configurado
        numero_nfce = venda.numero_venda
        serie_nfce = filial.serie_nfce or 1

    informacoes_adicionais = " | ".join(filter(None, [
        informacoes_adicionais.strip(),
        (getattr(doc_params, "informacoes_complementares", "") or "").strip(),
        (params.informacoes_complementares_padrao or "").strip(),
    ]))

    payload = NfcePayloadBuilder.build(
        venda,
        numero=numero_nfce,
        serie=serie_nfce,
        informacoes_adicionais=informacoes_adicionais,
    )
    if contingencia:
        payload["codigo_unico"] = f"{secrets.randbelow(100_000_000):08d}"

    doc = DocumentoFiscal.objects.create(
        filial=filial,
        tipo_documento="nfce",
        origem_tipo="venda_pdv",
        origem_id=venda.pk,
        numero=numero_nfce,
        serie=serie_nfce,
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
        valor_produtos=payload["valor_produtos"],
        valor_desconto=payload["valor_desconto"],
        valor_total=payload["valor_total"],
        status=StatusDocumentoFiscal.PENDENTE,
        data_emissao=_momento_emissao(venda),
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

    documento = service.emitir(doc, payload, contingencia=contingencia)
    venda.documento_fiscal = documento
    venda.save(update_fields=["documento_fiscal"])
    return documento


@transaction.atomic
def emitir_nfe_para_venda(venda: VendaPDV, usuario, *, informacoes_adicionais: str = "") -> DocumentoFiscal:
    """
    Wrapper de alto nível: constrói payload NF-e, cria DocumentoFiscal e dispara emissão.
    Usa ParametroDocumentoFiscal para série e número (reserva atômica).
    """
    from apps.fiscal.services.focusnfe_service import FocusNFeService
    from apps.fiscal.integrations.focusnfe import FocusNFeClient
    from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig
    from apps.core.models.parametros import ParametroDocumentoFiscal, ParametrosSistema

    filial = venda.filial

    existente = DocumentoFiscal.objects.filter(
        origem_tipo="venda_pdv",
        origem_id=venda.pk,
        tipo_documento="nfe",
    ).exclude(status=StatusDocumentoFiscal.CANCELADA).first()
    if existente and existente.status == StatusDocumentoFiscal.AUTORIZADA:
        return existente

    # Reserva número atômico via ParametroDocumentoFiscal
    params, _ = ParametrosSistema.objects.get_or_create(filial=filial)
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
        # Fallback se ainda não configurado
        from apps.core.models.empresa import Filial as _Filial
        filial_lock = _Filial.objects.select_for_update().get(pk=filial.pk)
        numero_nfe = filial_lock.proximo_numero_nfe
        serie_nfe = filial_lock.serie_nfe or 1
        filial_lock.proximo_numero_nfe = numero_nfe + 1
        filial_lock.save(update_fields=["proximo_numero_nfe"])

    informacoes_adicionais = " | ".join(filter(None, [
        informacoes_adicionais.strip(),
        (getattr(doc_params, "informacoes_complementares", "") or "").strip(),
        (params.informacoes_complementares_padrao or "").strip(),
    ]))

    payload = NfePayloadBuilder.build(
        venda,
        numero_nfe=numero_nfe,
        serie_nfe=serie_nfe,
        informacoes_adicionais=informacoes_adicionais,
    )

    doc = DocumentoFiscal.objects.create(
        filial=filial,
        tipo_documento="nfe",
        origem_tipo="venda_pdv",
        origem_id=venda.pk,
        numero=numero_nfe,
        serie=serie_nfe,
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
        valor_produtos=payload["valor_produtos"],
        valor_desconto=payload["valor_desconto"],
        valor_total=payload["valor_total"],
        status=StatusDocumentoFiscal.PENDENTE,
        data_emissao=_momento_emissao(venda),
        usuario=usuario,
    )

    filial_token = getattr(filial, "focusnfe_token", "") or ""
    filial_ambiente = getattr(filial, "focusnfe_ambiente", None)
    if filial_token:
        config = FocusNFeConfig.from_env(token=filial_token, ambiente=filial_ambiente)
        client = FocusNFeClient(config=config)
        service = FocusNFeService(client=client)
    else:
        service = FocusNFeService()

    documento = service.emitir(doc, payload)
    venda.documento_fiscal = documento
    venda.save(update_fields=["documento_fiscal"])
    return documento
