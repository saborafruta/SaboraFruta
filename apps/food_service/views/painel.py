"""Painel visual de mesas — quadro ao vivo + endpoint de polling (JSON)."""
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_GET

from apps.core.services.permissions import PermissaoRequiredMixin

from ..models import Comanda, Mesa


class PainelMesasView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'ver'

    def get(self, request):
        return render(request, 'food_service/painel_mesas.html', {
            'title': 'Painel de Mesas',
        })


def _resumo_mesa(mesa, agora):
    comanda_aberta = next(
        (c for c in mesa.comandas.all() if c.status == Comanda.Status.ABERTA), None,
    )
    dados = {
        'id': mesa.pk,
        'numero': mesa.numero,
        'nome': mesa.nome,
        'capacidade': mesa.capacidade,
        'setor': mesa.setor,
        'status': mesa.status,
        'comanda_id': None,
        'garcom': None,
        'cliente': None,
        'quantidade_pessoas': None,
        'aberta_ha_minutos': None,
        'valor_consumido': None,
    }
    if comanda_aberta:
        dados.update({
            'comanda_id': comanda_aberta.pk,
            'garcom': comanda_aberta.garcom.nome if comanda_aberta.garcom_id else None,
            'cliente': comanda_aberta.cliente.razao_social if comanda_aberta.cliente_id else comanda_aberta.nome_ocupante,
            'quantidade_pessoas': comanda_aberta.quantidade_pessoas,
            'aberta_ha_minutos': int((agora - comanda_aberta.aberta_em).total_seconds() // 60),
            'valor_consumido': str(comanda_aberta.valor_total),
        })
    return dados


@require_GET
def api_painel_mesas(request):
    if not request.user.is_authenticated:
        return JsonResponse({'erro': 'Sessão expirada.'}, status=401)
    if not request.user.tem_permissao('food_service', 'ver'):
        return JsonResponse({'erro': 'Você não tem permissão para esta ação.'}, status=403)

    agora = timezone.now()
    mesas = (
        Mesa.objects.for_filial(request.filial_ativa)
        .filter(ativo=True)
        .prefetch_related('comandas', 'comandas__garcom', 'comandas__cliente', 'comandas__itens')
        .order_by('numero')
    )
    return JsonResponse({
        'ok': True,
        'atualizado_em': agora.isoformat(),
        'mesas': [_resumo_mesa(mesa, agora) for mesa in mesas],
    })
