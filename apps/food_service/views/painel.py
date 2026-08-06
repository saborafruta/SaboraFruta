"""Painel visual de mesas — quadro ao vivo + endpoint de polling (JSON)."""
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_GET

from apps.core.services.exceptions import DadosInvalidosError
from apps.core.services.permissions import PermissaoRequiredMixin

from ..models import ChamadoMesa, Comanda, Mesa
from ..services import ChamadoService


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
            'pedidos_pendentes': sum(
                1 for p in comanda_aberta.pedidos_pendentes.all() if p.status == 'pendente'
            ),
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
        .prefetch_related(
            'comandas', 'comandas__garcom', 'comandas__cliente', 'comandas__itens',
            'comandas__pedidos_pendentes',
        )
        .order_by('numero')
    )
    return JsonResponse({
        'ok': True,
        'atualizado_em': agora.isoformat(),
        'mesas': [_resumo_mesa(mesa, agora) for mesa in mesas],
    })


@require_GET
def api_chamados_pendentes(request):
    if not request.user.is_authenticated:
        return JsonResponse({'erro': 'Sessão expirada.'}, status=401)
    if not request.user.tem_permissao('food_service', 'ver'):
        return JsonResponse({'erro': 'Você não tem permissão para esta ação.'}, status=403)

    chamados = (
        ChamadoMesa.objects.filter(mesa__filial=request.filial_ativa, status=ChamadoMesa.Status.PENDENTE)
        .select_related('mesa')
        .order_by('created_at')
    )
    return JsonResponse({
        'ok': True,
        'chamados': [
            {
                'id': c.pk,
                'mesa_numero': c.mesa.numero,
                'tipo': c.tipo,
                'tipo_display': c.get_tipo_display(),
                'ha_minutos': int((timezone.now() - c.created_at).total_seconds() // 60),
            }
            for c in chamados
        ],
    })


class ChamadoAtenderView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk):
        chamado = get_object_or_404(ChamadoMesa, pk=pk, mesa__filial=request.filial_ativa)
        try:
            ChamadoService.atender_chamado(chamado=chamado, usuario=request.user)
        except DadosInvalidosError as exc:
            messages.error(request, str(exc))
        return redirect(reverse('food_service:painel'))
