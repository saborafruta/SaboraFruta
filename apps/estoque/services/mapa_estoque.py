"""Visão por filial (semáforo) para o mapa de estoque."""
from __future__ import annotations

from decimal import Decimal

from apps.core.models import Filial
from apps.estoque.services.posicoes_estoque import calcular_posicoes

ZERO = Decimal("0")

# Pior classe presente decide a cor da filial -- uma filial com um só
# produto em ruptura já merece atenção, mesmo que o resto esteja bem.
_PESO_CLASSE = {"ruptura": 4, "critico": 3, "baixo": 2, "normal": 1, "alto": 0, "excesso": 0, "": 0}
_COR_POR_PESO = {4: "vermelho", 3: "vermelho", 2: "amarelo", 1: "verde", 0: "verde"}


def montar_mapa(*, empresa, dias_analise: int = 30, dias_cobertura: int = 14) -> list[dict]:
    posicoes = calcular_posicoes(empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura)
    por_filial: dict = {}
    for posicao in posicoes:
        filial = posicao["filial"]
        info = por_filial.setdefault(filial.pk, {
            "filial": filial, "peso_maximo": 0, "excesso": 0, "falta": 0,
            "valor_total": ZERO, "produtos": 0,
        })
        info["peso_maximo"] = max(info["peso_maximo"], _PESO_CLASSE.get(posicao["classe"], 0))
        info["produtos"] += 1
        info["valor_total"] += posicao["valor"]
        if posicao["excedente"] > ZERO:
            info["excesso"] += 1
        if posicao["deficit"] > ZERO:
            info["falta"] += 1

    filiais = Filial.objects.filter(empresa=empresa, ativo=True).order_by("-is_matriz", "nome_fantasia", "razao_social")
    mapa = []
    for filial in filiais:
        info = por_filial.get(filial.pk, {
            "filial": filial, "peso_maximo": 0, "excesso": 0, "falta": 0,
            "valor_total": ZERO, "produtos": 0,
        })
        info["cor"] = _COR_POR_PESO.get(info["peso_maximo"], "verde")
        mapa.append(info)
    return mapa


def detalhe_filial(*, empresa, filial_id, dias_analise: int = 30, dias_cobertura: int = 14) -> dict:
    posicoes = calcular_posicoes(
        empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura, filial_id=filial_id,
    )
    em_excesso = sorted((p for p in posicoes if p["excedente"] > ZERO), key=lambda p: p["excedente"], reverse=True)
    em_falta = sorted((p for p in posicoes if p["deficit"] > ZERO), key=lambda p: p["deficit"], reverse=True)
    valor_total = sum((p["valor"] for p in posicoes), ZERO)
    coberturas = [p["cobertura"] for p in posicoes if p["cobertura"] is not None]
    cobertura_media = (sum(coberturas, ZERO) / len(coberturas)).quantize(Decimal("0.1")) if coberturas else None
    return {
        "em_excesso": em_excesso,
        "em_falta": em_falta,
        "valor_total": valor_total,
        "cobertura_media": cobertura_media,
        "total_produtos": len(posicoes),
    }
