"""KDS -- Kitchen Display System: fila de itens em preparo, tempo real via polling."""
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_GET

from apps.core.models import Notificacao
from apps.core.services.exceptions import DadosInvalidosError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.produtos.models import Produto

from ..models import Comanda, ItemComanda
from ..services import KdsService
from ..services.notificacao_service import notificar_item_atrasado, notificar_produto_indisponivel


class KdsView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'ver'

    def get(self, request):
        # Só lista pra "tornar disponível" os produtos marcados indisponíveis
        # por essa tela mesma (via notificação ativa) -- não todo o catálogo
        # inativo, que pode ter itens descontinuados sem relação com a cozinha.
        ids_marcados_indisponiveis = Notificacao.objects.filter(
            filial=request.filial_ativa,
            tipo=Notificacao.Tipo.FOOD_PRODUTO_INDISPONIVEL,
            ativa=True,
        ).values_list('referencia_id', flat=True)
        produtos_indisponiveis = Produto.objects.for_filial(request.filial_ativa).filter(
            pk__in=list(ids_marcados_indisponiveis), ativo=False,
        ).order_by('descricao')

        return render(request, 'food_service/kds.html', {
            'title': 'Cozinha (KDS)',
            'produtos_ativos': Produto.objects.for_filial(request.filial_ativa).filter(ativo=True).order_by('descricao'),
            'produtos_indisponiveis': produtos_indisponiveis,
        })


_ATIVOS = [
    ItemComanda.StatusPreparo.RECEBIDO,
    ItemComanda.StatusPreparo.EM_PREPARO,
    ItemComanda.StatusPreparo.QUASE_PRONTO,
    ItemComanda.StatusPreparo.PRONTO,
]


def _resumo_item(item, agora, posicao):
    comanda = item.comanda
    mesas = ', '.join(str(m) for m in comanda.mesas.all()) or 'avulsa'
    referencia = item.recebido_em or item.adicionado_em
    return {
        'id': item.pk,
        'posicao': posicao,
        'produto': item.produto.descricao,
        'categoria': item.produto.categoria.nome if item.produto.categoria_id else 'Outros',
        'tipo_produto': item.produto.tipo_produto,
        'quantidade': str(item.quantidade),
        'observacoes': item.observacoes,
        'status_preparo': item.status_preparo,
        'status_display': item.get_status_preparo_display(),
        'prioridade': item.prioridade,
        'comanda_id': comanda.pk,
        'mesas': mesas,
        'garcom': comanda.garcom.nome if comanda.garcom_id else None,
        'ocupante': comanda.cliente.razao_social if comanda.cliente_id else comanda.nome_ocupante,
        'minutos_decorridos': int((agora - referencia).total_seconds() // 60) if referencia else None,
        'complementos': [
            f'{c.quantidade}x {c.produto.descricao}' for c in item.complementos.all()
        ],
    }


@require_GET
def api_kds(request):
    if not request.user.is_authenticated:
        return JsonResponse({'erro': 'Sessão expirada.'}, status=401)
    if not request.user.tem_permissao('food_service', 'ver'):
        return JsonResponse({'erro': 'Você não tem permissão para esta ação.'}, status=403)

    agora = timezone.now()
    itens = (
        ItemComanda.objects
        .filter(
            comanda__filial=request.filial_ativa,
            comanda__status=Comanda.Status.ABERTA,
            status_preparo__in=_ATIVOS,
        )
        .select_related('produto', 'produto__categoria', 'comanda', 'comanda__garcom', 'comanda__cliente')
        .prefetch_related('comanda__mesas', 'complementos__produto')
    )
    itens_ordenados = KdsService.fila_ordenada(itens)

    # Atraso é checado aqui (não tem worker/cron) -- update_or_create pela
    # referencia_id evita duplicar a notificação a cada poll de 10s.
    for item in itens_ordenados:
        if KdsService.prazo(item) < agora:
            notificar_item_atrasado(item)

    return JsonResponse({
        'ok': True,
        'itens': [
            _resumo_item(item, agora, posicao)
            for posicao, item in enumerate(itens_ordenados, start=1)
        ],
    })


def _item_da_filial(request, pk):
    return get_object_or_404(
        ItemComanda.objects.select_related('comanda'),
        pk=pk, comanda__filial=request.filial_ativa,
    )


class KdsAvancarStatusView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk):
        item = _item_da_filial(request, pk)
        try:
            KdsService.avancar_status(item=item, novo_status=request.POST.get('status', ''), usuario=request.user)
        except DadosInvalidosError as exc:
            return JsonResponse({'erro': str(exc)}, status=400)
        return JsonResponse({'ok': True})


class KdsAlterarPrioridadeView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk):
        item = _item_da_filial(request, pk)
        try:
            prioridade = int(request.POST.get('prioridade', '0'))
            KdsService.alterar_prioridade(item=item, prioridade=prioridade)
        except (ValueError, DadosInvalidosError) as exc:
            return JsonResponse({'erro': str(exc)}, status=400)
        return JsonResponse({'ok': True})


class KdsCancelarItemView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk):
        item = _item_da_filial(request, pk)
        try:
            KdsService.cancelar_item(item=item)
        except DadosInvalidosError as exc:
            return JsonResponse({'erro': str(exc)}, status=400)
        return JsonResponse({'ok': True})


class ProdutoIndisponivelView(PermissaoRequiredMixin, View):
    """
    "Acabou" -- tira o produto de venda em todo o sistema (PDV, cardápio
    digital, comandas), não só da cozinha. Reaproveita o campo `ativo` que
    já existe e já é respeitado em todo canal de venda.
    """

    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request):
        produto = get_object_or_404(Produto.objects.for_filial(request.filial_ativa), pk=request.POST.get('produto_id'))
        produto.ativo = False
        produto.save(update_fields=['ativo'])
        notificar_produto_indisponivel(produto, request.filial_ativa)
        return redirect(reverse('food_service:kds'))


class ProdutoDisponivelView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk):
        produto = get_object_or_404(Produto.objects.for_filial(request.filial_ativa), pk=pk)
        produto.ativo = True
        produto.save(update_fields=['ativo'])
        Notificacao.objects.filter(
            filial=request.filial_ativa,
            tipo=Notificacao.Tipo.FOOD_PRODUTO_INDISPONIVEL,
            referencia_tipo='produto',
            referencia_id=str(produto.pk),
        ).update(ativa=False)
        return redirect(reverse('food_service:kds'))
