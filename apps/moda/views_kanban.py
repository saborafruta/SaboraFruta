"""
Kanban de produção (grupo Produção).

A movimentação responde JSON porque o arrasto não pode recarregar a página:
o encarregado move vários cartões seguidos, e um recarregamento a cada
solta perderia a rolagem horizontal do quadro a cada vez.
"""
import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from apps.core.services.exceptions import DomainError

from .models import OrdemProducao
from .services.kanban import COLUNAS, KanbanService
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


class KanbanView(ModaBaseView):
    def get(self, request):
        filtros = {
            'cliente': (request.GET.get('cliente') or '').strip(),
            'produto': (request.GET.get('produto') or '').strip(),
            'prioridade': (request.GET.get('prioridade') or '').strip(),
        }
        dados = KanbanService.quadro(_filial(request), filtros)

        return render(request, 'moda/kanban.html', {
            'title': 'Kanban de Produção',
            'filtros': filtros,
            'tem_filtro': any(filtros.values()),
            'prioridades': OrdemProducao.Prioridade.choices,
            # Sem permissão de editar, o quadro vira leitura: os cartões não
            # ganham `draggable` e nenhum POST é possível.
            'pode_mover': request.user.tem_permissao('moda', 'editar'),
            **dados,
        })


class KanbanMoverView(ModaBaseView):
    """Recebe a solta do cartão. A permissão real é conferida no serviço."""

    permissao_acao = 'ver'

    def post(self, request, pk):
        ordem = get_object_or_404(
            OrdemProducao.objects.for_filial(_filial(request)).prefetch_related('etapas'),
            pk=pk,
        )

        try:
            corpo = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            return JsonResponse({'ok': False, 'erro': 'Requisição inválida.'}, status=400)

        try:
            resultado = KanbanService.mover(
                ordem, (corpo.get('coluna') or '').strip(), request.user,
            )
        except DomainError as erro:
            # 400 e não 500: é regra de negócio recusando, não falha do
            # sistema — e o JavaScript devolve o cartão para a coluna antiga.
            return JsonResponse({'ok': False, 'erro': str(erro)}, status=400)

        return JsonResponse({
            'ok': True,
            'mensagem': self.mensagem(ordem, resultado),
            **resultado,
        })

    @staticmethod
    def mensagem(ordem, resultado: dict) -> str:
        rotulo = next(
            (c.label for c in COLUNAS if c.chave == resultado['coluna']),
            resultado['coluna'],
        )
        partes = [f'{ordem.numero} → {rotulo}']

        if resultado['concluidas']:
            partes.append(
                'Concluída(s) automaticamente: ' + ', '.join(resultado['concluidas']) + '.'
            )
        if resultado['reabertas']:
            partes.append(
                'Reaberta(s): ' + ', '.join(resultado['reabertas'])
                + ' — os números apontados foram mantidos.'
            )
        return ' '.join(partes)
