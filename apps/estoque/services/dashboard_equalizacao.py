"""Cards-resumo do dashboard de equalização de estoque."""
from __future__ import annotations

from decimal import Decimal

from apps.core.models import Filial
from apps.estoque.models import ConferenciaTransferencia
from apps.estoque.services.equilibrio_estoque import calcular_equilibrio
from apps.estoque.services.posicoes_estoque import calcular_posicoes, custo_unitario

ZERO = Decimal("0")

# "Aprovada" no dashboard conta qualquer etapa entre a criação da
# transferência (o estoque já foi movido nesse ponto) e a chegada no
# destino -- não existe hoje uma etapa de aprovação manual anterior ao
# movimento, então "aprovada" é o guarda-chuva de tudo que já saiu da
# origem e ainda não foi recebido/cancelado.
ETAPAS_APROVADA_OU_POSTERIOR = (
    ConferenciaTransferencia.Etapa.APROVADA,
    ConferenciaTransferencia.Etapa.SEPARANDO,
    ConferenciaTransferencia.Etapa.EXPEDIDA,
    ConferenciaTransferencia.Etapa.EM_TRANSITO,
)


def montar_dashboard(*, empresa, dias_analise: int = 30, dias_cobertura: int = 14) -> dict:
    posicoes = calcular_posicoes(empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura)
    resultado_transferencias = calcular_equilibrio(empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura)
    sugestoes = resultado_transferencias["sugestoes"]

    estoque_total = sum((p["valor"] for p in posicoes), ZERO)
    estoque_excedente = sum((p["excedente"] * p["custo_unitario"] for p in posicoes), ZERO)
    estoque_critico = sum((p["valor"] for p in posicoes if p["classe"] in ("ruptura", "critico")), ZERO)
    capital_parado = sum((p["valor"] for p in posicoes if p["parado"]), ZERO)

    produtos_ruptura = {p["produto"].pk for p in posicoes if p["classe"] == "ruptura"}
    produtos_criticos = {p["produto"].pk for p in posicoes if p["classe"] == "critico"}
    produtos_parados = {p["produto"].pk for p in posicoes if p["parado"]}

    coberturas = [p["cobertura"] for p in posicoes if p["cobertura"] is not None]
    cobertura_media = (sum(coberturas, ZERO) / len(coberturas)).quantize(Decimal("0.1")) if coberturas else None

    valor_recuperavel = sum((s["quantidade"] * custo_unitario(s["produto"]) for s in sugestoes), ZERO)

    filial_ids = list(Filial.objects.filter(empresa=empresa, ativo=True).values_list("pk", flat=True))
    conferencias_ativas = ConferenciaTransferencia.objects.filter(filial_origem_id__in=filial_ids)
    transferencias_aprovadas = conferencias_ativas.filter(etapa__in=ETAPAS_APROVADA_OU_POSTERIOR).count()
    transferencias_em_transito = conferencias_ativas.filter(etapa=ConferenciaTransferencia.Etapa.EM_TRANSITO).count()

    return {
        "estoque_total": estoque_total,
        "estoque_excedente": estoque_excedente,
        "estoque_critico": estoque_critico,
        "produtos_ruptura": len(produtos_ruptura),
        "produtos_criticos": len(produtos_criticos),
        "produtos_parados": len(produtos_parados),
        "transferencias_sugeridas": len(sugestoes),
        "transferencias_aprovadas": transferencias_aprovadas,
        "transferencias_em_transito": transferencias_em_transito,
        "valor_recuperavel": valor_recuperavel,
        "capital_parado": capital_parado,
        "cobertura_media": cobertura_media,
    }
