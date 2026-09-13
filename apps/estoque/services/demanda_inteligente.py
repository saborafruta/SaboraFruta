"""
Inteligência de demanda (Fase 16): média ponderada por período configurável,
índice por dia da semana e comparação sazonal com o mesmo período do ano
anterior.

Deliberadamente separado de `equilibrio_estoque.calcular_equilibrio`: é uma
leitura adicional para quem quer entender a demanda com mais nuance, não
substitui o `demanda_diaria` já testado que o motor de equalização usa em
produção -- trocar isso ali exigiria revalidar todos os cálculos de meta já
em uso.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.estoque.models import ConfiguracaoDemandaPonderada
from apps.pdv.models import ItemVendaPDV
from apps.vendas.models import ItemPedidoVenda

ZERO = Decimal("0")
PERIODOS = (7, 15, 30, 60, 90)

PESOS_PADRAO = {7: 0, 15: 0, 30: 50, 60: 30, 90: 20}


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def carregar_pesos(*, empresa) -> dict:
    config = ConfiguracaoDemandaPonderada.objects.filter(empresa=empresa).first()
    return config.como_dict() if config else dict(PESOS_PADRAO)


def calcular_demanda_ponderada(*, produto_id, filial_id, pesos: dict, agora=None) -> dict:
    """
    Para cada período com peso > 0, calcula a venda média diária daquele
    período; a demanda ponderada é a média dessas médias, pesada pelo
    peso de cada período (normalizada pela soma dos pesos usados).
    """
    agora = agora or timezone.now()
    detalhamento = []
    soma_pesos = ZERO
    soma_ponderada = ZERO
    for dias in PERIODOS:
        peso = _decimal(pesos.get(dias, 0))
        if peso <= ZERO:
            continue
        inicio = agora - timezone.timedelta(days=dias)
        vendido = _decimal(
            ItemVendaPDV.objects.filter(
                produto_id=produto_id, venda_pdv__filial_id=filial_id, venda_pdv__status="finalizada",
                venda_pdv__data_venda__gte=inicio, venda_pdv__data_venda__lte=agora,
            ).aggregate(total=Sum("quantidade"))["total"]
        )
        vendido += _decimal(
            ItemPedidoVenda.objects.filter(
                produto_id=produto_id, pedido__filial_id=filial_id,
                pedido__data_emissao__gte=inicio, pedido__data_emissao__lte=agora,
            ).aggregate(total=Sum("quantidade"))["total"]
        )
        media_diaria = (vendido / Decimal(dias)).quantize(Decimal("0.001"))
        detalhamento.append({"dias": dias, "peso": pesos.get(dias, 0), "vendido": vendido, "media_diaria": media_diaria})
        soma_ponderada += media_diaria * peso
        soma_pesos += peso

    demanda_ponderada = (soma_ponderada / soma_pesos).quantize(Decimal("0.001")) if soma_pesos > ZERO else ZERO
    return {"detalhamento": detalhamento, "demanda_ponderada": demanda_ponderada}


DIAS_SEMANA_LABEL = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]


def calcular_indice_dia_semana(*, produto_id, filial_id, dias_analise: int = 180, agora=None) -> list[dict]:
    """
    Índice > 1 = vende acima da média nesse dia da semana; < 1 = abaixo.
    Usa só PDV (venda de balcão é a que tem data/hora granular o
    suficiente pra dia da semana fazer sentido; B2B costuma ser combinado
    por telefone/pedido, não pelo dia em que o cliente "decide comprar").
    """
    agora = agora or timezone.now()
    inicio = agora - timezone.timedelta(days=dias_analise)
    totais_por_dia = defaultdict(lambda: ZERO)
    for item in ItemVendaPDV.objects.filter(
        produto_id=produto_id, venda_pdv__filial_id=filial_id, venda_pdv__status="finalizada",
        venda_pdv__data_venda__gte=inicio, venda_pdv__data_venda__lte=agora,
    ).values_list("venda_pdv__data_venda", "quantidade"):
        data_venda, quantidade = item
        totais_por_dia[data_venda.weekday()] += _decimal(quantidade)

    total_geral = sum(totais_por_dia.values(), ZERO)
    media_por_dia = total_geral / 7 if total_geral > ZERO else ZERO
    resultado = []
    for dia in range(7):
        total_dia = totais_por_dia.get(dia, ZERO)
        indice = (total_dia / media_por_dia).quantize(Decimal("0.01")) if media_por_dia > ZERO else None
        resultado.append({"dia": dia, "label": DIAS_SEMANA_LABEL[dia], "vendido": total_dia, "indice": indice})
    return resultado


def calcular_sazonalidade(*, produto_id, filial_id, dias_analise: int = 30, agora=None) -> dict:
    """
    Compara o total vendido no período atual com o mesmo período de 365
    dias atrás (mesma duração, deslocado um ano). Índice > 1 = vendendo
    mais que no ano passado nessa época; None = sem venda no período
    anterior pra comparar (produto novo ou sazonal demais pra ter base).
    """
    agora = agora or timezone.now()
    inicio_atual = agora - timezone.timedelta(days=dias_analise)
    inicio_anterior = inicio_atual - timezone.timedelta(days=365)
    fim_anterior = agora - timezone.timedelta(days=365)

    def _total(inicio, fim):
        return _decimal(
            ItemVendaPDV.objects.filter(
                produto_id=produto_id, venda_pdv__filial_id=filial_id, venda_pdv__status="finalizada",
                venda_pdv__data_venda__gte=inicio, venda_pdv__data_venda__lte=fim,
            ).aggregate(total=Sum("quantidade"))["total"]
        )

    vendido_atual = _total(inicio_atual, agora)
    vendido_ano_anterior = _total(inicio_anterior, fim_anterior)
    indice = (vendido_atual / vendido_ano_anterior).quantize(Decimal("0.01")) if vendido_ano_anterior > ZERO else None
    return {
        "vendido_atual": vendido_atual,
        "vendido_ano_anterior": vendido_ano_anterior,
        "indice": indice,
    }
