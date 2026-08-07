"""Visualizacao de alertas operacionais de estoque."""
from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import DecimalField, F, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.estoque.models import AlertaVencimento, Estoque
from apps.estoque.services.desperdicio_service import DesperdicioService
from apps.produtos.models import Produto

NIVEIS_ALERTA = [
    ('d1',   'Urgente 1d'),
    ('d7',   'Crítico 7d'),
    ('d30',  'Alto 30d'),
    ('d60',  'Médio 60d'),
    ('d90',  'Atenção 90d'),
    ('d180', 'Aviso 180d'),
    ('critico', 'Crítico (legado)'),
    ('alto',    'Alto (legado)'),
    ('medio',   'Médio (legado)'),
    ('baixo',   'Baixo (legado)'),
]


def _alerta_vencimento(alerta):
    return {
        'tipo': 'vencimento',
        'tipo_label': 'Vencimento',
        'nivel': alerta.nivel_risco,
        'nivel_label': alerta.get_nivel_risco_display(),
        'produto': alerta.produto,
        'lote': alerta.lote,
        'quantidade': alerta.quantidade_em_risco,
        'data': alerta.data_validade,
        'dias': alerta.dias_para_vencer,
        'tem_prazo': alerta.dias_para_vencer is not None,
        'mensagem': 'Lote proximo do vencimento',
    }


def _alerta_minimo(produto):
    nivel = 'critico' if produto.estoque_quantidade_disponivel <= 0 else 'alto'
    return {
        'tipo': 'minimo',
        'tipo_label': 'Estoque minimo',
        'nivel': nivel,
        'nivel_label': 'Critico' if nivel == 'critico' else 'Alto',
        'produto': produto,
        'lote': None,
        'quantidade': produto.estoque_quantidade_disponivel,
        'data': None,
        'dias': None,
        'tem_prazo': False,
        'mensagem': f'Minimo configurado: {produto.estoque_minimo}',
    }


def _alerta_parado(estoque):
    from django.utils import timezone
    hoje = timezone.now()
    referencias = [d for d in (estoque.ultima_entrada, estoque.ultima_saida) if d]
    dias = (hoje - max(referencias)).days if referencias else None
    return {
        'tipo': 'parado',
        'tipo_label': 'Estoque parado',
        'nivel': 'medio',
        'nivel_label': 'Medio',
        'produto': estoque.produto,
        'lote': None,
        'quantidade': estoque.quantidade_atual,
        'data': None,
        'dias': dias,
        'tem_prazo': dias is not None,
        'mensagem': 'Sem nenhuma movimentacao ha' + (f' {dias} dias' if dias is not None else ' muito tempo'),
    }


def _alerta_sem_giro(estoque):
    from django.utils import timezone
    hoje = timezone.now()
    dias = (hoje - estoque.ultima_saida).days if estoque.ultima_saida else None
    return {
        'tipo': 'sem_giro',
        'tipo_label': 'Sem giro',
        'nivel': 'baixo',
        'nivel_label': 'Baixo',
        'produto': estoque.produto,
        'lote': None,
        'quantidade': estoque.quantidade_atual,
        'data': None,
        'dias': dias,
        'tem_prazo': dias is not None,
        'mensagem': 'Sem saida ha' + (f' {dias} dias' if dias is not None else ' nunca vendido/consumido'),
    }


class AlertaListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    template_name = 'estoque/alerta/list.html'

    def get(self, request):
        tipo = request.GET.get('tipo', '')
        nivel = request.GET.get('nivel', '')

        alertas = []
        if tipo in {'', 'vencimento'}:
            vencimentos = AlertaVencimento.objects.for_filial(request.filial_ativa).filter(
                resolvido=False,
            ).select_related('produto', 'lote').order_by('data_validade')
            alertas.extend(_alerta_vencimento(alerta) for alerta in vencimentos)

        if tipo in {'', 'minimo'}:
            estoque_qs = Estoque.objects.filter(
                produto=OuterRef('pk'),
                filial=request.filial_ativa,
            )
            quantidade_field = DecimalField(max_digits=12, decimal_places=3)
            minimos = Produto.objects.for_filial(request.filial_ativa).filter(
                ativo=True,
                estoque_minimo__gt=0,
            ).annotate(
                estoque_quantidade_disponivel=Coalesce(
                    Subquery(
                        estoque_qs.values('quantidade_disponivel')[:1],
                        output_field=quantidade_field,
                    ),
                    Value(Decimal('0'), output_field=quantidade_field),
                    output_field=quantidade_field,
                ),
            ).filter(
                estoque_quantidade_disponivel__lt=F('estoque_minimo')
            )
            alertas.extend(_alerta_minimo(produto) for produto in minimos)

        if tipo in {'', 'parado'}:
            parados = DesperdicioService.produtos_parados(request.filial_ativa)
            alertas.extend(_alerta_parado(estoque) for estoque in parados)

        if tipo in {'', 'sem_giro'}:
            sem_giro = DesperdicioService.produtos_sem_giro(request.filial_ativa)
            alertas.extend(_alerta_sem_giro(estoque) for estoque in sem_giro)

        if nivel:
            alertas = [alerta for alerta in alertas if alerta['nivel'] == nivel]

        ordem_nivel = {'critico': 0, 'alto': 1, 'medio': 2, 'baixo': 3}
        alertas.sort(
            key=lambda item: (
                ordem_nivel.get(item['nivel'], 9),
                item['dias'] is None,
                item['dias'] or 9999,
            )
        )

        page_obj = Paginator(alertas, 50).get_page(request.GET.get('page'))
        querydict = request.GET.copy()
        querydict.pop('page', None)

        return render(request, self.template_name, {
            'page_obj': page_obj,
            'alertas': page_obj.object_list,
            'tipo': tipo,
            'nivel': nivel,
            'niveis': NIVEIS_ALERTA,
            'page_querystring': querydict.urlencode(),
            'total_alertas': len(alertas),
            'total_criticos': sum(1 for item in alertas if item['nivel'] == 'critico'),
            'total_minimos': sum(1 for item in alertas if item['tipo'] == 'minimo'),
            'total_vencimentos': sum(1 for item in alertas if item['tipo'] == 'vencimento'),
            'total_parados': sum(1 for item in alertas if item['tipo'] == 'parado'),
            'total_sem_giro': sum(1 for item in alertas if item['tipo'] == 'sem_giro'),
        })
