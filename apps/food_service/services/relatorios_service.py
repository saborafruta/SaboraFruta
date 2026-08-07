"""Relatorios Gerenciais -- reaproveita os servicos ja construidos ao longo
do modulo (CmvService, KdsIndicadoresService, FluxoCaixaService) e cobre
com agregacao nova so o que ainda nao existia: faturamento/ticket
medio/canal, permanencia de mesa, avaliacao media, ocupacao, horarios de
pico, produtividade por garcom e comparativo entre periodos.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from statistics import mean

from django.db.models import Avg, Count, DecimalField, F, Sum
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek, TruncYear
from django.utils import timezone

from apps.food_service.models import AvaliacaoAtendimento, Comanda, ItemComanda, Mesa
from apps.food_service.services.cmv_service import CmvService
from apps.food_service.services.kds_indicadores_service import KdsIndicadoresService
from apps.financeiro.services.fluxo_caixa_service import FluxoCaixaService

DIAS_SEMANA = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']

TURNOS = {
    'madrugada': (0, 6, 'Madrugada'),
    'manha': (6, 12, 'Manhã'),
    'tarde': (12, 18, 'Tarde'),
    'noite': (18, 24, 'Noite'),
}


class RelatoriosGerenciaisService:

    @staticmethod
    def _comandas_fechadas(filial, data_inicio, data_fim, turno=None, colaborador_id=None):
        qs = Comanda.objects.filter(
            filial=filial, status=Comanda.Status.FECHADA,
            fechada_em__date__gte=data_inicio, fechada_em__date__lte=data_fim,
        ).select_related('venda_pdv', 'cliente', 'garcom').prefetch_related('mesas')
        if turno:
            inicio_h, fim_h, _ = TURNOS[turno]
            qs = qs.filter(aberta_em__hour__gte=inicio_h, aberta_em__hour__lt=fim_h)
        if colaborador_id:
            qs = qs.filter(garcom_id=colaborador_id)
        return qs

    @staticmethod
    def _canal(comanda):
        if comanda.tipo == Comanda.Tipo.QR_CODE:
            return 'QR Code'
        if comanda.venda_pdv_id and comanda.venda_pdv.delivery:
            return 'Delivery'
        if comanda.tipo in (Comanda.Tipo.MESA, Comanda.Tipo.COMPARTILHADA):
            return 'Salão'
        return 'Balcão'

    # ── Vendas ───────────────────────────────────────────────────────────────

    @classmethod
    def vendas(cls, filial, data_inicio, data_fim, turno=None, colaborador_id=None):
        comandas = list(cls._comandas_fechadas(filial, data_inicio, data_fim, turno, colaborador_id))

        faturamento_total = sum(
            (c.venda_pdv.valor_total for c in comandas if c.venda_pdv_id), Decimal('0'),
        )
        numero_pedidos = len(comandas)
        ticket_medio = (faturamento_total / numero_pedidos) if numero_pedidos else None
        clientes_distintos = len({c.cliente_id for c in comandas if c.cliente_id})
        pessoas_atendidas = sum((c.quantidade_pessoas for c in comandas), 0)

        canais = {}
        for c in comandas:
            canal = cls._canal(c)
            grupo = canais.setdefault(canal, {'canal': canal, 'pedidos': 0, 'valor': Decimal('0')})
            grupo['pedidos'] += 1
            grupo['valor'] += c.venda_pdv.valor_total if c.venda_pdv_id else Decimal('0')
        por_canal = sorted(canais.values(), key=lambda g: g['valor'], reverse=True)

        return {
            'faturamento_total': faturamento_total,
            'numero_pedidos': numero_pedidos,
            'ticket_medio': ticket_medio,
            'clientes_distintos': clientes_distintos,
            'pessoas_atendidas': pessoas_atendidas,
            'por_canal': por_canal,
        }

    @staticmethod
    def faturamento_serie(filial, granularidade='dia', periodos=12):
        """Tendencia de faturamento (diaria/semanal/mensal/anual), independente
        do periodo filtrado no resto do relatorio -- sempre olhando pra tras
        a partir de hoje."""
        trunc = {'dia': TruncDate, 'semana': TruncWeek, 'mes': TruncMonth, 'ano': TruncYear}[granularidade]
        janela_dias = {'dia': periodos, 'semana': periodos * 7, 'mes': periodos * 31, 'ano': periodos * 366}[granularidade]
        desde = timezone.localdate() - timedelta(days=janela_dias)

        agregados = (
            Comanda.objects.filter(
                filial=filial, status=Comanda.Status.FECHADA,
                fechada_em__date__gte=desde, venda_pdv__isnull=False,
            )
            .annotate(periodo=trunc('fechada_em'))
            .values('periodo')
            .annotate(valor=Sum('venda_pdv__valor_total'), pedidos=Count('id'))
            .order_by('periodo')
        )
        return list(agregados)

    # ── Produtos ─────────────────────────────────────────────────────────────

    @classmethod
    def produtos(cls, filial, data_inicio, data_fim, turno=None, top=10):
        itens_qs = ItemComanda.objects.filter(
            comanda__filial=filial, comanda__status=Comanda.Status.FECHADA,
            comanda__fechada_em__date__gte=data_inicio, comanda__fechada_em__date__lte=data_fim,
        ).exclude(status_preparo=ItemComanda.StatusPreparo.CANCELADO)
        if turno:
            inicio_h, fim_h, _ = TURNOS[turno]
            itens_qs = itens_qs.filter(comanda__aberta_em__hour__gte=inicio_h, comanda__aberta_em__hour__lt=fim_h)

        agregado = list(
            itens_qs.annotate(
                linha_valor=F('quantidade') * F('valor_unitario'),
            )
            .values('produto_id', 'produto__descricao')
            .annotate(
                quantidade_total=Sum('quantidade'),
                receita=Sum('linha_valor', output_field=DecimalField(max_digits=14, decimal_places=2)),
            )
            .order_by('-quantidade_total')
        )
        for linha in agregado:
            linha['quantidade'] = linha.pop('quantidade_total')

        cmv_resumo = CmvService.resumo(filial, data_inicio, data_fim)

        return {
            'mais_vendidos': agregado[:top],
            'menos_vendidos': sorted(agregado, key=lambda x: x['quantidade'])[:top],
            'mais_lucrativos': sorted(cmv_resumo['por_prato'], key=lambda g: g['margem'], reverse=True)[:top],
            'maior_cmv': sorted(
                [g for g in cmv_resumo['por_prato'] if g['cmv_percentual'] is not None],
                key=lambda g: g['cmv_percentual'], reverse=True,
            )[:top],
        }

    # ── Atendimento ──────────────────────────────────────────────────────────

    @classmethod
    def atendimento(cls, filial, data_inicio, data_fim, turno=None, colaborador_id=None):
        comandas = cls._comandas_fechadas(filial, data_inicio, data_fim, turno, colaborador_id)
        duracoes = [
            (c.fechada_em - c.aberta_em).total_seconds() / 60
            for c in comandas if c.fechada_em
        ]
        tempo_medio_permanencia = round(mean(duracoes), 1) if duracoes else None

        kds = KdsIndicadoresService.resumo(filial, data_inicio, data_fim)

        avaliacoes = AvaliacaoAtendimento.objects.filter(
            comanda__filial=filial,
            comanda__fechada_em__date__gte=data_inicio, comanda__fechada_em__date__lte=data_fim,
        )
        agregado_notas = avaliacoes.aggregate(media=Avg('nota'), total=Count('id'))

        from apps.pdv.models.venda import VendaPDV
        entregas = VendaPDV.objects.filter(
            filial=filial, delivery=True, delivery_encerrado_em__isnull=False,
            created_at__date__gte=data_inicio, created_at__date__lte=data_fim,
        )
        duracoes_entrega = [
            (v.delivery_encerrado_em - v.created_at).total_seconds() / 60 for v in entregas
        ]

        return {
            'tempo_medio_permanencia': tempo_medio_permanencia,
            'tempo_medio_preparo': kds['tempo_medio_geral'],
            'sla_preparo_percentual': kds['sla_percentual'],
            'produtividade_cozinha': kds['por_cozinheiro'],
            'nota_media': round(agregado_notas['media'], 2) if agregado_notas['media'] else None,
            'total_avaliacoes': agregado_notas['total'],
            'tempo_medio_entrega': round(mean(duracoes_entrega), 1) if duracoes_entrega else None,
            'total_entregas_com_tempo': len(duracoes_entrega),
        }

    # ── Operação ─────────────────────────────────────────────────────────────

    @classmethod
    def operacao(cls, filial, data_inicio, data_fim, turno=None, colaborador_id=None):
        comandas = list(cls._comandas_fechadas(filial, data_inicio, data_fim, turno, colaborador_id))

        giros_mesa = {}
        for c in comandas:
            for mesa in c.mesas.all():
                grupo = giros_mesa.setdefault(mesa.pk, {'mesa': mesa, 'giros': 0, 'faturamento': Decimal('0')})
                grupo['giros'] += 1
                grupo['faturamento'] += c.venda_pdv.valor_total if c.venda_pdv_id else Decimal('0')
        giros_por_mesa = sorted(giros_mesa.values(), key=lambda g: g['giros'], reverse=True)

        mesas_status = list(
            Mesa.objects.filter(filial=filial, ativo=True).values('status').annotate(total=Count('id'))
        )

        horarios = Counter(c.aberta_em.hour for c in comandas)
        horarios_pico = sorted(horarios.items(), key=lambda kv: kv[1], reverse=True)[:5]

        dias = Counter(c.aberta_em.weekday() for c in comandas)
        dias_movimentados = sorted(
            ({'dia': DIAS_SEMANA[k], 'pedidos': v} for k, v in dias.items()),
            key=lambda d: d['pedidos'], reverse=True,
        )

        garcons = {}
        for c in comandas:
            if not c.garcom_id:
                continue
            grupo = garcons.setdefault(c.garcom_id, {'nome': c.garcom.nome, 'pedidos': 0, 'faturamento': Decimal('0')})
            grupo['pedidos'] += 1
            grupo['faturamento'] += c.venda_pdv.valor_total if c.venda_pdv_id else Decimal('0')
        produtividade_garcom = sorted(garcons.values(), key=lambda g: g['faturamento'], reverse=True)

        return {
            'giros_por_mesa': giros_por_mesa,
            'mesas_status': mesas_status,
            'horarios_pico': horarios_pico,
            'dias_movimentados': dias_movimentados,
            'produtividade_garcom': produtividade_garcom,
        }

    # ── Financeiro ───────────────────────────────────────────────────────────

    @classmethod
    def financeiro(cls, filial, data_inicio, data_fim):
        from apps.financeiro.models.dre import DREConsolidado

        fluxo = FluxoCaixaService.apurar(filial, data_inicio, data_fim)
        cmv = CmvService.resumo(filial, data_inicio, data_fim)
        dre_recente = DREConsolidado.objects.filter(filial=filial).order_by('-competencia').first()

        return {
            'fluxo': fluxo,
            'cmv_percentual': cmv['cmv_percentual'],
            'margem_contribuicao': cmv['margem_contribuicao'],
            'lucro_bruto': cmv['lucro_bruto'],
            'lucro_liquido': cmv['lucro_liquido'],
            'por_prato': cmv['por_prato'],
            'por_categoria': cmv['por_categoria'],
            'dre_recente': dre_recente,
            'comparativo': cls._comparativo(filial, data_inicio, data_fim),
        }

    @staticmethod
    def _comparativo(filial, data_inicio: date, data_fim: date):
        dias = (data_fim - data_inicio).days + 1
        fim_anterior = data_inicio - timedelta(days=1)
        inicio_anterior = fim_anterior - timedelta(days=dias - 1)

        def _totais(inicio, fim):
            agregado = Comanda.objects.filter(
                filial=filial, status=Comanda.Status.FECHADA,
                fechada_em__date__gte=inicio, fechada_em__date__lte=fim, venda_pdv__isnull=False,
            ).aggregate(faturamento=Sum('venda_pdv__valor_total'), pedidos=Count('id'))
            return {
                'inicio': inicio, 'fim': fim,
                'faturamento': agregado['faturamento'] or Decimal('0'),
                'pedidos': agregado['pedidos'] or 0,
            }

        atual = _totais(data_inicio, data_fim)
        anterior = _totais(inicio_anterior, fim_anterior)
        variacao = None
        if anterior['faturamento']:
            variacao = (atual['faturamento'] - anterior['faturamento']) / anterior['faturamento'] * 100

        return {'periodo_atual': atual, 'periodo_anterior': anterior, 'variacao_percentual': variacao}
