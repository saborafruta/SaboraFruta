"""KDS -- Kitchen Display System: fila de itens em preparo, tempo real via polling."""
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_GET

from apps.core.services.exceptions import DadosInvalidosError
from apps.core.services.permissions import PermissaoRequiredMixin

from ..models import Comanda, ItemComanda
from ..services import KdsService


class KdsView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'ver'

    def get(self, request):
        return render(request, 'food_service/kds.html', {'title': 'Cozinha (KDS)'})


_ATIVOS = [
    ItemComanda.StatusPreparo.RECEBIDO,
    ItemComanda.StatusPreparo.EM_PREPARO,
    ItemComanda.StatusPreparo.QUASE_PRONTO,
    ItemComanda.StatusPreparo.PRONTO,
]


def _resumo_item(item, agora):
    comanda = item.comanda
    mesas = ', '.join(str(m) for m in comanda.mesas.all()) or 'avulsa'
    referencia = item.recebido_em or item.adicionado_em
    return {
        'id': item.pk,
        'produto': item.produto.descricao,
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
        .select_related('produto', 'comanda', 'comanda__garcom', 'comanda__cliente')
        .prefetch_related('comanda__mesas', 'complementos__produto')
        .order_by('-prioridade', 'recebido_em')
    )
    return JsonResponse({
        'ok': True,
        'itens': [_resumo_item(item, agora) for item in itens],
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
            KdsService.avancar_status(item=item, novo_status=request.POST.get('status', ''))
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
