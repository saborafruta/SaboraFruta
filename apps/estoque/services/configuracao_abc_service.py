"""Carrega os ajustes de meta de estoque por classe da curva ABC (giro)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from apps.estoque.models import ConfiguracaoAbcEstoque

UM = Decimal("1")


@dataclass(frozen=True)
class AjusteAbc:
    multiplicador_minimo: Decimal
    multiplicador_maximo: Decimal
    dias_cobertura_extra: int


AJUSTE_PADRAO = AjusteAbc(UM, UM, 0)


def carregar_ajustes_abc(*, empresa) -> dict:
    """Uma linha por classe (A/B/C); classe sem linha cadastrada usa o padrão (sem ajuste)."""
    ajustes = {}
    for linha in ConfiguracaoAbcEstoque.objects.filter(empresa=empresa):
        ajustes[linha.classe] = AjusteAbc(
            linha.multiplicador_minimo, linha.multiplicador_maximo, linha.dias_cobertura_extra,
        )
    return ajustes


def resolver_ajuste_abc(ajustes: dict, classe) -> AjusteAbc:
    return ajustes.get(classe, AJUSTE_PADRAO)
