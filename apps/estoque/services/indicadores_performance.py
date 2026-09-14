"""
Indicadores de performance da equalização (Fase 28).

Metade vem do ESTADO ATUAL (calcular_posicoes, sem histórico nenhum);
a outra metade depende de HISTÓRICO real (transferências já executadas,
snapshots já tirados) -- fica None/"—" quando não há dado suficiente
ainda, em vez de estimar um número que ninguém pediu.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.core.models import Filial
from apps.estoque.models import ConferenciaTransferencia, MovimentacaoEstoque, SugestaoEqualizacaoSnapshot
from apps.estoque.services.posicoes_estoque import calcular_posicoes, custo_unitario
from apps.produtos.models import Produto

ZERO = Decimal("0")
CEM = Decimal("100")


def calcular_indicadores(*, empresa, dias_analise: int = 30, dias_cobertura: int = 14, dias_historico: int = 30) -> dict:
    posicoes = calcular_posicoes(empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura)
    total_posicoes = len(posicoes)

    def _pct(qtd):
        return (Decimal(qtd) / total_posicoes * CEM).quantize(Decimal("0.1")) if total_posicoes else None

    estoque_total = sum((p["valor"] for p in posicoes), ZERO)
    estoque_excedente = sum((p["excedente"] * p["custo_unitario"] for p in posicoes), ZERO)
    capital_parado = sum((p["valor"] for p in posicoes if p["parado"]), ZERO)
    vendido_valor = sum((p["vendido"] * p["custo_unitario"] for p in posicoes), ZERO)

    coberturas = [p["cobertura"] for p in posicoes if p["cobertura"] is not None]
    cobertura_media = (sum(coberturas, ZERO) / len(coberturas)).quantize(Decimal("0.1")) if coberturas else None

    indicadores_atuais = {
        "taxa_ruptura_pct": _pct(sum(1 for p in posicoes if p["classe"] == "ruptura")),
        "cobertura_media_dias": cobertura_media,
        "giro_estoque": (vendido_valor / estoque_total).quantize(Decimal("0.01")) if estoque_total > ZERO else None,
        "capital_parado": capital_parado,
        "pct_estoque_excedente": (estoque_excedente / estoque_total * CEM).quantize(Decimal("0.1")) if estoque_total > ZERO else None,
        "pct_estoque_abaixo_minimo": _pct(sum(1 for p in posicoes if p["deficit"] > ZERO)),
    }

    filial_ids = list(Filial.objects.filter(empresa=empresa, ativo=True).values_list("pk", flat=True))
    inicio_historico = timezone.now() - timezone.timedelta(days=dias_historico)

    transferencias_realizadas = ConferenciaTransferencia.objects.filter(
        filial_origem_id__in=filial_ids, etapa=ConferenciaTransferencia.Etapa.RECEBIDA,
        created_at__gte=inicio_historico,
    ).count()

    movs_saida = MovimentacaoEstoque.objects.filter(
        filial_id__in=filial_ids, tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
        data_movimentacao__gte=inicio_historico, transferencia_cancelada=False,
    )
    valor_redistribuido = ZERO
    agregados_produto = movs_saida.values("produto_id").annotate(total=Sum("quantidade"))
    if agregados_produto:
        produtos_por_id = {p.pk: p for p in Produto.objects.filter(pk__in=[r["produto_id"] for r in agregados_produto])}
        for row in agregados_produto:
            produto = produtos_por_id.get(row["produto_id"])
            if produto:
                valor_redistribuido += Decimal(str(row["total"])) * custo_unitario(produto)

    snapshots = SugestaoEqualizacaoSnapshot.objects.filter(empresa=empresa, created_at__gte=inicio_historico)
    total_snapshots = snapshots.count()
    acuracidade = None
    transferencias_nao_aplicadas = None
    if total_snapshots:
        aplicadas = 0
        for snap in snapshots.select_related("produto", "filial_origem", "filial_destino"):
            existe = MovimentacaoEstoque.objects.filter(
                produto_id=snap.produto_id, filial_id=snap.filial_origem_id,
                filial_destino_id=snap.filial_destino_id,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
                data_movimentacao__gte=snap.created_at,
            ).exists()
            aplicadas += 1 if existe else 0
        acuracidade = (Decimal(aplicadas) / total_snapshots * CEM).quantize(Decimal("0.1"))
        transferencias_nao_aplicadas = total_snapshots - aplicadas

    indicadores_historico = {
        "dias_historico": dias_historico,
        "transferencias_realizadas": transferencias_realizadas,
        "valor_redistribuido": valor_redistribuido.quantize(Decimal("0.01")),
        "total_sugestoes_geradas": total_snapshots,
        "transferencias_nao_aplicadas": transferencias_nao_aplicadas,
        "acuracidade_recomendacao_pct": acuracidade,
    }

    return {"atuais": indicadores_atuais, "historico": indicadores_historico}
