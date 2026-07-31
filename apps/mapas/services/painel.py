"""
Indicadores do painel de mapas (§14).

Uma ressalva vale por todo o arquivo: **km e tempo são de rotas calculadas, não
de percurso real**. O sistema não sabe por onde o veículo passou — isso depende
do rastreamento (§13), que está em standby. Chamar de "percorrido" um número
que é "planejado" faria o painel mentir sobre a operação, então os rótulos
dizem o que o número é.
"""
from __future__ import annotations

import datetime

from django.db.models import Count, Q, Sum
from django.utils import timezone


class PainelService:
    """Os nove indicadores da especificação, agrupados por origem do dado."""

    @staticmethod
    def _escopo(filial):
        from apps.mapas.services import ProximidadeService

        return ProximidadeService._escopo_filiais(filial)

    @classmethod
    def indicadores(cls, filial, *, inicio=None, fim=None):
        hoje = timezone.localdate()
        fim = fim or hoje
        inicio = inicio or fim
        filiais = cls._escopo(filial)

        dados = {
            'inicio': inicio.isoformat(),
            'fim': fim.isoformat(),
            'periodo_e_hoje': inicio == fim == hoje,
        }
        dados.update(cls._cobertura(filiais))
        dados.update(cls._entregas(filiais, inicio, fim))
        dados.update(cls._rotas(filiais, inicio, fim))
        dados.update(cls._sugestoes(filiais, inicio, fim))
        return dados

    # ── cadastro ──────────────────────────────────────────────────────────
    @staticmethod
    def _cobertura(filiais):
        """Clientes cadastrados, geolocalizados e sem coordenada."""
        from apps.cadastros.models import Cliente

        agg = Cliente.objects.filter(filial__in=filiais, ativo=True).aggregate(
            total=Count('id'),
            com=Count('id', filter=Q(latitude__isnull=False)),
        )
        total, com = agg['total'] or 0, agg['com'] or 0
        return {
            'clientes_cadastrados': total,
            'clientes_geolocalizados': com,
            'clientes_sem_coordenada': total - com,
            'cobertura_pct': round(com / total * 100, 1) if total else 0.0,
        }

    # ── operação de entrega ───────────────────────────────────────────────
    @staticmethod
    def _entregas(filiais, inicio, fim):
        """
        Entregas do período e clientes visitados.

        "Visitado" é o cliente distinto de uma entrega que chegou a `entregue`
        ou `finalizado` — o pedido que ainda está em preparo não visitou
        ninguém. Contar clientes distintos, e não pedidos, evita que dois
        pedidos para o mesmo endereço virem duas visitas.
        """
        from apps.pdv.models import VendaPDV

        base = VendaPDV.objects.filter(
            filial__in=filiais, delivery=True,
            data_venda__date__gte=inicio, data_venda__date__lte=fim,
        ).exclude(status='cancelada').exclude(status_delivery='cancelado')

        entregues = base.filter(status_delivery__in=['entregue', 'finalizado'])

        return {
            'entregas_periodo': base.count(),
            'entregas_concluidas': entregues.count(),
            'clientes_visitados': (
                entregues.exclude(cliente__isnull=True)
                .values('cliente_id').distinct().count()
            ),
        }

    # ── rotas calculadas ──────────────────────────────────────────────────
    @staticmethod
    def _rotas(filiais, inicio, fim):
        """
        Km, tempo e economia das rotas montadas no período.

        A economia sai de `distancia_antes_m − distancia_m` somada só das
        rotas otimizadas. Somar a diferença linha a linha (e não comparar
        totais) é o que mantém o número honesto quando parte das rotas não
        passou pela otimização.
        """
        from apps.mapas.models import RegistroRota

        qs = RegistroRota.objects.filter(
            filial__in=filiais,
            created_at__date__gte=inicio, created_at__date__lte=fim,
        )
        agg = qs.aggregate(
            distancia=Sum('distancia_m'), duracao=Sum('duracao_s'), n=Count('id'),
        )
        economia_m = sum(r.economia_m for r in qs.filter(otimizada=True))

        distancia = agg['distancia'] or 0
        return {
            'rotas_calculadas': agg['n'] or 0,
            'km_em_rota': round(distancia / 1000, 1),
            'tempo_em_rota_s': agg['duracao'] or 0,
            'tempo_em_rota_texto': PainelService.formatar_duracao(agg['duracao'] or 0),
            'economia_km': round(economia_m / 1000, 1),
            # Percentual sobre a distância que teria sido feita sem otimizar.
            'economia_pct': (
                round(economia_m / (distancia + economia_m) * 100, 1)
                if (distancia + economia_m) else 0.0
            ),
        }

    # ── sugestões oferecidas ──────────────────────────────────────────────
    @staticmethod
    def _sugestoes(filiais, inicio, fim):
        from apps.mapas.models import SugestaoProximidade

        agg = SugestaoProximidade.objects.filter(
            filial__in=filiais,
            created_at__date__gte=inicio, created_at__date__lte=fim,
        ).aggregate(total=Sum('total'), n=Count('id'))

        return {
            'clientes_sugeridos': agg['total'] or 0,
            'consultas_de_sugestao': agg['n'] or 0,
        }

    @staticmethod
    def formatar_duracao(segundos):
        """`5400` -> `'1h30'`; menos de uma hora vira `'45min'`."""
        segundos = int(segundos or 0)
        horas, minutos = divmod(segundos // 60, 60)
        if not horas:
            return f'{minutos}min'
        return f'{horas}h{minutos:02d}' if minutos else f'{horas}h'

    @staticmethod
    def periodo_de(request):
        """
        Lê `?de=&ate=` da querystring; sem parâmetros, o dia de hoje.

        Data inválida cai no padrão em vez de erro: o painel abrir com o dia
        corrente é melhor que uma tela de erro por causa de um link torto.
        """
        def _data(valor):
            try:
                return datetime.date.fromisoformat(valor)
            except (TypeError, ValueError):
                return None

        hoje = timezone.localdate()
        inicio = _data(request.GET.get('de')) or hoje
        fim = _data(request.GET.get('ate')) or hoje
        if fim < inicio:
            inicio, fim = fim, inicio
        return inicio, fim
