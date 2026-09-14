"""Grava um retrato das sugestões do equilíbrio -- a única escrita no banco que o motor de equalização faz, e só quando explicitamente pedida (nunca durante uma leitura de tela)."""
from __future__ import annotations

from apps.estoque.models import SugestaoEqualizacaoSnapshot
from apps.estoque.services.equilibrio_estoque import calcular_equilibrio


def gerar_snapshot_equilibrio(empresa, *, dias_analise: int = 30, dias_cobertura: int = 14) -> int:
    resultado = calcular_equilibrio(empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura)
    linhas = [
        SugestaoEqualizacaoSnapshot(
            empresa=empresa, produto=sugestao["produto"],
            filial_origem=sugestao["origem"], filial_destino=sugestao["destino"],
            quantidade_sugerida=sugestao["quantidade"], score=sugestao["score"],
            destino_meta=sugestao["destino_meta"],
        )
        for sugestao in resultado["sugestoes"]
    ]
    SugestaoEqualizacaoSnapshot.objects.bulk_create(linhas)
    return len(linhas)
