"""
Enriquecimento de uma sugestão do equilíbrio para a tabela de
recomendações: campos que são só apresentação (cobertura após a
transferência, valor, SKU, prioridade, explicação em texto) e por isso
ficam fora de `equilibrio_estoque.calcular_equilibrio` -- não influenciam
em nada o cálculo real de quanto/para onde transferir.
"""
from __future__ import annotations

from decimal import Decimal

from apps.estoque.services.equilibrio_estoque import _cobertura
from apps.estoque.services.posicoes_estoque import custo_unitario

ZERO = Decimal("0")


def _nome_filial(filial) -> str:
    return filial.nome_fantasia or filial.razao_social


def _prioridade_label(score: int) -> str:
    if score >= 70:
        return "Alta"
    if score >= 40:
        return "Média"
    return "Baixa"


def gerar_motivo(sugestao: dict) -> str:
    """Espera uma sugestão já enriquecida por `enriquecer_para_tabela` (usa `destino_demanda_diaria`)."""
    origem_nome = _nome_filial(sugestao["origem"])
    destino_nome = _nome_filial(sugestao["destino"])
    quantidade = sugestao["quantidade"].normalize()

    razoes = []
    if sugestao["origem_cobertura"] is not None:
        razoes.append(
            f"a Filial {origem_nome} possui {sugestao['origem_cobertura']} dias de cobertura, "
            "acima do que a própria demanda dela precisa"
        )
    elif sugestao["produto_parado_origem"]:
        razoes.append(f"o produto está parado na Filial {origem_nome} (nenhuma venda no período analisado)")

    if sugestao["destino_cobertura"] is not None:
        razoes.append(
            f"a Filial {destino_nome} possui apenas {sugestao['destino_cobertura']} dia(s) de cobertura "
            f"e vende aproximadamente {sugestao['destino_demanda_diaria']} unidades por dia"
        )
    elif sugestao["destino_sem_estoque"]:
        razoes.append(f"a Filial {destino_nome} está sem estoque deste produto")

    if not razoes:
        razoes.append("a rede possui excedente deste produto que pode ser redistribuído")

    return (
        f"Recomendamos transferir {quantidade} unidades da Filial {origem_nome} para a Filial {destino_nome} "
        f"porque {', enquanto '.join(razoes)}."
    )


def enriquecer_para_tabela(sugestao: dict, *, dias_analise: int) -> dict:
    """Copia a sugestão e acrescenta os campos exclusivos da tabela de recomendações."""
    divisor = Decimal(dias_analise)
    origem_demanda_diaria = sugestao["origem_vendido"] / divisor
    destino_demanda_diaria = (sugestao["destino_vendido"] / divisor).quantize(Decimal("0.1"))
    custo = custo_unitario(sugestao["produto"])

    enriquecida = dict(sugestao)
    enriquecida["sku"] = sugestao["produto"].codigo or str(sugestao["produto"].codigo_replicacao)
    enriquecida["destino_demanda_diaria"] = destino_demanda_diaria
    enriquecida["origem_cobertura_apos"] = _cobertura(sugestao["origem_saldo"] - sugestao["quantidade"], origem_demanda_diaria)
    enriquecida["destino_cobertura_apos"] = _cobertura(sugestao["destino_saldo"] + sugestao["quantidade"], destino_demanda_diaria)
    enriquecida["custo_unitario"] = custo
    enriquecida["valor_transferencia"] = (sugestao["quantidade"] * custo).quantize(Decimal("0.01"))
    enriquecida["prioridade_label"] = _prioridade_label(sugestao["score"])
    enriquecida["status_label"] = "Sugerida"
    enriquecida["motivo"] = gerar_motivo(enriquecida)
    return enriquecida
