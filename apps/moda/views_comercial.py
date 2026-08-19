"""
A tela do Comercial: o quadro de pedidos.

ANTES ERA UM MENU DE CARTÕES, e um menu não responde a pergunta que o
comercial faz ao abrir o sistema — "onde está cada pedido agora". O quadro
responde: cada pedido é um cartão, cada coluna é um momento do fluxo, e a
posição do cartão é o `status` do próprio pedido, não um estado inventado
aqui.

As telas do grupo (Clientes, Orçamentos, Pedidos...) continuam alcançáveis
pela barra do topo. Elas não sumiram: deixaram de ser a primeira coisa que a
pessoa vê, porque abrir o comercial para escolher um submenu é um passo a
mais antes de qualquer trabalho.

A movimentação responde JSON: o vendedor arrasta vários cartões seguidos, e
recarregar a página a cada solta perderia a rolagem horizontal do quadro.
"""
import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import NoReverseMatch, Resolver404, resolve, reverse

from apps.core.services.exceptions import DomainError

from .menu import GRUPOS_POR_SLUG
from .models import PedidoProducao
from .services.kanban_comercial import COLUNAS, KanbanComercialService
from .views import ModaBaseView, itens_com_tela


def _filial(request):
    return request.filial_ativa


class ComercialView(ModaBaseView):
    """Quadro de pedidos — a porta de entrada do grupo Comercial."""

    area = 'comercial'

    def get(self, request):
        filtros = {
            'busca': (request.GET.get('busca') or '').strip(),
            'prioridade': (request.GET.get('prioridade') or '').strip(),
            'vendedor': (request.GET.get('vendedor') or '').strip(),
            'atrasados': request.GET.get('atrasados') == '1',
        }
        dados = KanbanComercialService.quadro(_filial(request), filtros)
        grupo = GRUPOS_POR_SLUG['comercial']

        return render(request, 'moda/kanban_comercial.html', {
            'title': 'Comercial',
            'grupo': grupo,
            'prontos': itens_com_tela(grupo),
            'filtros': filtros,
            'tem_filtro': any(filtros.values()),
            'prioridades': PedidoProducao.Prioridade.choices,
            'colunas': COLUNAS,
            # Sem permissão de editar, o quadro é leitura: os cartões não
            # ganham `draggable` e nenhum POST passa pelo serviço.
            'pode_mover': request.user.tem_permissao('moda', 'editar'),
            **dados,
        })


class ComercialMoverView(ModaBaseView):
    """Recebe a solta do cartão. A permissão real é conferida no serviço."""

    area = 'comercial'
    permissao_acao = 'ver'

    def post(self, request, pk):
        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request))
            .select_related('cliente')
            .prefetch_related('itens__personalizacoes'),
            pk=pk,
        )

        try:
            corpo = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            return JsonResponse({'ok': False, 'erro': 'Requisição inválida.'}, status=400)

        try:
            resultado = KanbanComercialService.mover(
                pedido, (corpo.get('coluna') or '').strip(), request.user,
            )
        except DomainError as erro:
            # 400 e não 500: é regra de negócio recusando, não falha do
            # sistema — e o JavaScript devolve o cartão para a coluna antiga
            # em vez de deixar a tela mentir sobre onde o pedido está.
            return JsonResponse({'ok': False, 'erro': str(erro)}, status=400)

        return JsonResponse({
            'ok': True,
            'mensagem': self.mensagem(pedido, resultado),
            **resultado,
        })

    @staticmethod
    def mensagem(pedido, resultado: dict) -> str:
        if not resultado['mudou']:
            return f'#{pedido.numero:06d} já estava nesta coluna.'

        partes = [
            f'#{pedido.numero:06d}: {resultado["anterior"]} → {resultado["atual"]}.'
        ]
        partes.extend(resultado['avisos'])
        return ' '.join(partes)
