"""Simulador 'e se': prévia de antes/depois de uma transferência manual, antes de aplicá-la de verdade."""
from __future__ import annotations

from decimal import Decimal

from apps.estoque.services.cobertura_service import CLASSE_LABEL, carregar_faixas, classificar_cobertura, resolver_faixa
from apps.estoque.services.equilibrio_estoque import _cobertura
from apps.estoque.services.posicoes_estoque import calcular_posicoes, custo_unitario

ZERO = Decimal("0")

# Mesmos pesos do mapa de estoque -- usados aqui só para dizer se o
# destino "sobe de classe" (ex.: sai de crítico pra baixo) depois da
# transferência simulada.
_PESO_CLASSE = {"ruptura": 4, "critico": 3, "baixo": 2, "normal": 1, "alto": 0, "excesso": 0, "": 0}


def simular_transferencia(
    *, empresa, produto_id, origem_id, destino_id, quantidade: Decimal,
    dias_analise: int = 30, dias_cobertura: int = 14,
) -> dict | None:
    posicoes = calcular_posicoes(empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura)
    posicao_origem = next((p for p in posicoes if p["produto"].pk == produto_id and p["filial"].pk == origem_id), None)
    posicao_destino = next((p for p in posicoes if p["produto"].pk == produto_id and p["filial"].pk == destino_id), None)
    if posicao_origem is None or posicao_destino is None:
        return None

    saldo_origem_depois = posicao_origem["saldo"] - quantidade
    saldo_destino_depois = posicao_destino["saldo"] + quantidade
    cobertura_origem_depois = _cobertura(saldo_origem_depois, posicao_origem["demanda_diaria"])
    cobertura_destino_depois = _cobertura(saldo_destino_depois, posicao_destino["demanda_diaria"])

    reducao_excesso_dias = None
    if posicao_origem["cobertura"] is not None and cobertura_origem_depois is not None:
        reducao_excesso_dias = posicao_origem["cobertura"] - cobertura_origem_depois

    peso_antes = _PESO_CLASSE.get(posicao_destino["classe"], 0)
    faixa = resolver_faixa(carregar_faixas(empresa=empresa), posicao_destino["produto"])
    classe_destino_depois = classificar_cobertura(cobertura_destino_depois, faixa)
    peso_depois = _PESO_CLASSE.get(classe_destino_depois, 0)
    reduz_risco_ruptura = peso_depois < peso_antes

    custo = custo_unitario(posicao_origem["produto"])

    return {
        "produto": posicao_origem["produto"],
        "origem": posicao_origem["filial"],
        "destino": posicao_destino["filial"],
        "quantidade": quantidade,
        "excede_saldo_origem": quantidade > posicao_origem["saldo"],
        "origem_saldo_antes": posicao_origem["saldo"],
        "origem_cobertura_antes": posicao_origem["cobertura"],
        "destino_saldo_antes": posicao_destino["saldo"],
        "destino_cobertura_antes": posicao_destino["cobertura"],
        "destino_classe_antes": posicao_destino["classe_label"],
        "origem_saldo_depois": saldo_origem_depois,
        "origem_cobertura_depois": cobertura_origem_depois,
        "destino_saldo_depois": saldo_destino_depois,
        "destino_cobertura_depois": cobertura_destino_depois,
        "destino_classe_depois": classe_destino_depois,
        "destino_classe_depois_label": CLASSE_LABEL.get(classe_destino_depois, ""),
        "reducao_excesso_dias": reducao_excesso_dias,
        "reduz_risco_ruptura": reduz_risco_ruptura,
        "capital_redistribuido": (quantidade * custo).quantize(Decimal("0.01")),
    }
