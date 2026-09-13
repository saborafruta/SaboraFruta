"""Classificação de cobertura de estoque (dias) em faixas configuráveis."""
from __future__ import annotations

from dataclasses import dataclass

from apps.estoque.models import FaixaCoberturaEstoque

DIAS_CRITICO_PADRAO = 3
DIAS_BAIXO_PADRAO = 7
DIAS_NORMAL_PADRAO = 15
DIAS_ALTO_PADRAO = 30


@dataclass(frozen=True)
class Faixa:
    dias_critico: int
    dias_baixo: int
    dias_normal: int
    dias_alto: int


FAIXA_PADRAO = Faixa(DIAS_CRITICO_PADRAO, DIAS_BAIXO_PADRAO, DIAS_NORMAL_PADRAO, DIAS_ALTO_PADRAO)

CLASSE_LABEL = {
    "ruptura": "Ruptura",
    "critico": "Crítico",
    "baixo": "Baixo",
    "normal": "Normal",
    "alto": "Alto",
    "excesso": "Excesso",
}


def carregar_faixas(*, empresa) -> dict:
    """
    Pré-carrega, numa única consulta, todas as faixas configuradas da
    empresa -- resolver a cascata produto > categoria > padrão por produto
    dentro do loop do equilíbrio faria uma query por produto.
    """
    por_produto: dict = {}
    por_categoria: dict = {}
    padrao_empresa = None
    for linha in FaixaCoberturaEstoque.objects.filter(empresa=empresa):
        faixa = Faixa(linha.dias_critico, linha.dias_baixo, linha.dias_normal, linha.dias_alto)
        if linha.produto_id:
            por_produto[linha.produto_id] = faixa
        elif linha.categoria_id:
            por_categoria[linha.categoria_id] = faixa
        else:
            padrao_empresa = faixa
    return {"produto": por_produto, "categoria": por_categoria, "empresa": padrao_empresa}


def resolver_faixa(faixas: dict, produto) -> Faixa:
    especifica = faixas["produto"].get(produto.pk)
    if especifica:
        return especifica
    if produto.categoria_id:
        da_categoria = faixas["categoria"].get(produto.categoria_id)
        if da_categoria:
            return da_categoria
    return faixas["empresa"] or FAIXA_PADRAO


def classificar_cobertura(dias, faixa: Faixa = FAIXA_PADRAO) -> str:
    """
    dias=None significa "sem giro no período" -- não é uma das seis faixas
    (que descrevem cobertura de uma demanda real); quem exibe decide como
    tratar esse caso (normalmente como aviso à parte, não como classe).
    """
    if dias is None:
        return ""
    if dias <= 0:
        return "ruptura"
    if dias <= faixa.dias_critico:
        return "critico"
    if dias <= faixa.dias_baixo:
        return "baixo"
    if dias <= faixa.dias_normal:
        return "normal"
    if dias <= faixa.dias_alto:
        return "alto"
    return "excesso"
