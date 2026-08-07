from __future__ import annotations

from statistics import mean

from django.utils import timezone

from apps.food_service.models import Comanda, ItemComanda
from apps.food_service.services.kds_service import KdsService

_CONCLUIDOS = [ItemComanda.StatusPreparo.PRONTO, ItemComanda.StatusPreparo.ENTREGUE]
_ATIVOS = [
    ItemComanda.StatusPreparo.RECEBIDO,
    ItemComanda.StatusPreparo.EM_PREPARO,
    ItemComanda.StatusPreparo.QUASE_PRONTO,
]


class KdsIndicadoresService:
    """
    Indicadores de tempo de preparo: tempo médio por prato e por cozinheiro,
    pedidos atrasados, SLA de produção e gargalos da cozinha.

    Calculado em Python (não em agregação SQL) porque cada indicador cruza
    campos diferentes por item (duração real vs. tempo esperado do produto,
    que pode estar em branco) -- para o volume de um dia de restaurante isso
    é simples de ler e barato de rodar.
    """

    @staticmethod
    def resumo(filial, data_inicio, data_fim):
        concluidos = list(
            ItemComanda.objects
            .filter(
                comanda__filial=filial,
                recebido_em__date__gte=data_inicio,
                recebido_em__date__lte=data_fim,
                status_preparo__in=_CONCLUIDOS,
                recebido_em__isnull=False,
                pronto_em__isnull=False,
            )
            .select_related('produto', 'preparado_por')
        )

        linhas = []
        for item in concluidos:
            duracao_total_min = (item.pronto_em - item.recebido_em).total_seconds() / 60
            duracao_preparo_min = (
                (item.pronto_em - item.iniciado_em).total_seconds() / 60
                if item.iniciado_em else None
            )
            tempo_esperado = item.produto.tempo_preparo_minutos or KdsService.TEMPO_PREPARO_PADRAO_MINUTOS
            linhas.append({
                'item': item,
                'duracao_total_min': duracao_total_min,
                'duracao_preparo_min': duracao_preparo_min,
                'tempo_esperado': tempo_esperado,
                'atrasado': duracao_total_min > tempo_esperado,
            })

        atrasados_agora = ItemComanda.objects.filter(
            comanda__filial=filial,
            comanda__status=Comanda.Status.ABERTA,
            status_preparo__in=_ATIVOS,
        ).select_related('produto')
        pedidos_atrasados_agora = sum(
            1 for item in atrasados_agora if KdsService.prazo(item) < timezone.now()
        )

        total = len(linhas)
        no_prazo = sum(1 for linha in linhas if not linha['atrasado'])

        por_prato = {}
        for linha in linhas:
            nome = linha['item'].produto.descricao
            grupo = por_prato.setdefault(nome, {'nome': nome, 'duracoes': [], 'atrasados': 0, 'tempo_esperado': linha['tempo_esperado']})
            grupo['duracoes'].append(linha['duracao_total_min'])
            if linha['atrasado']:
                grupo['atrasados'] += 1

        resumo_pratos = []
        for grupo in por_prato.values():
            tempo_medio = mean(grupo['duracoes'])
            resumo_pratos.append({
                'nome': grupo['nome'],
                'quantidade': len(grupo['duracoes']),
                'tempo_medio': round(tempo_medio, 1),
                'tempo_esperado': grupo['tempo_esperado'],
                'atrasados': grupo['atrasados'],
                'desvio': round(tempo_medio - grupo['tempo_esperado'], 1),
            })
        resumo_pratos.sort(key=lambda g: g['tempo_medio'], reverse=True)

        por_cozinheiro = {}
        for linha in linhas:
            if not linha['item'].preparado_por_id or linha['duracao_preparo_min'] is None:
                continue
            nome = linha['item'].preparado_por.nome
            grupo = por_cozinheiro.setdefault(nome, [])
            grupo.append(linha['duracao_preparo_min'])

        resumo_cozinheiros = sorted(
            (
                {'nome': nome, 'quantidade': len(duracoes), 'tempo_medio': round(mean(duracoes), 1)}
                for nome, duracoes in por_cozinheiro.items()
            ),
            key=lambda g: g['tempo_medio'],
        )

        gargalos = sorted(
            [g for g in resumo_pratos if g['desvio'] > 0],
            key=lambda g: g['desvio'], reverse=True,
        )[:5]

        return {
            'total_concluidos': total,
            'tempo_medio_geral': round(mean(l['duracao_total_min'] for l in linhas), 1) if linhas else None,
            'sla_percentual': round(100 * no_prazo / total, 1) if total else None,
            'pedidos_atrasados_concluidos': total - no_prazo,
            'pedidos_atrasados_agora': pedidos_atrasados_agora,
            'por_prato': resumo_pratos,
            'por_cozinheiro': resumo_cozinheiros,
            'gargalos': gargalos,
        }
