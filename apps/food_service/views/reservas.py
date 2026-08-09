"""Reservas futuras de mesa."""
from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_GET

from apps.cadastros.models import Cliente
from apps.core.services.permissions import PermissaoRequiredMixin

from ..models import Mesa, Reserva


@require_GET
def api_clientes_busca(request):
    """Autocomplete de clientes cadastrados para a Nova Reserva."""
    if not request.user.is_authenticated:
        return JsonResponse({'erro': 'Sessão expirada.'}, status=401)
    if not request.user.tem_permissao('food_service', 'criar'):
        return JsonResponse({'erro': 'Você não tem permissão para esta ação.'}, status=403)

    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'clientes': []})

    clientes = (
        Cliente.objects.for_filial(request.filial_ativa)
        .filter(ativo=True)
        .filter(
            Q(razao_social__icontains=q)
            | Q(nome_fantasia__icontains=q)
            | Q(celular__icontains=q)
            | Q(telefone__icontains=q)
        )
        .order_by('razao_social')[:8]
    )
    return JsonResponse({
        'clientes': [
            {
                'id': c.pk,
                'nome': c.nome_fantasia or c.razao_social,
                'telefone': c.celular or c.telefone or '',
            }
            for c in clientes
        ],
    })


class ReservaListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'ver'

    def get(self, request):
        reservas = (
            Reserva.objects.for_filial(request.filial_ativa)
            .filter(status=Reserva.Status.CONFIRMADA)
            .select_related('mesa', 'cliente')
            .order_by('data_hora')
        )
        return render(request, 'food_service/reserva_list.html', {
            'title': 'Reservas',
            'reservas': reservas,
        })


class ReservaCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'criar'

    def get(self, request):
        return self._render(request)

    def _render(self, request, dados=None):
        return render(request, 'food_service/reserva_form.html', {
            'title': 'Nova Reserva',
            'mesas': Mesa.objects.for_filial(request.filial_ativa).filter(ativo=True).order_by('numero'),
            'dados': dados or {},
        })

    def post(self, request):
        data_hora = request.POST.get('data_hora', '').strip()
        if not data_hora:
            messages.error(request, 'Informe data e hora da reserva.')
            return self._render(request, request.POST)

        mesa = None
        mesa_id = request.POST.get('mesa_id', '').strip()
        if mesa_id:
            mesa = Mesa.objects.for_filial(request.filial_ativa).filter(pk=mesa_id).first()

        cliente = None
        cliente_id = request.POST.get('cliente_id', '').strip()
        if cliente_id:
            cliente = Cliente.objects.for_filial(request.filial_ativa).filter(pk=cliente_id).first()

        quantidade_pessoas = request.POST.get('quantidade_pessoas', '2').strip()
        quantidade_pessoas = int(quantidade_pessoas) if quantidade_pessoas.isdigit() else 2

        Reserva.objects.create(
            filial=request.filial_ativa,
            mesa=mesa,
            cliente=cliente,
            nome_contato=request.POST.get('nome_contato', '').strip(),
            telefone=request.POST.get('telefone', '').strip(),
            data_hora=data_hora,
            quantidade_pessoas=quantidade_pessoas,
            observacoes=request.POST.get('observacoes', '').strip(),
        )
        messages.success(request, 'Reserva criada.')
        return redirect(reverse('food_service:reserva-list'))


class ReservaCancelarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk):
        reserva = get_object_or_404(Reserva.objects.for_filial(request.filial_ativa), pk=pk)
        reserva.status = Reserva.Status.CANCELADA
        reserva.save(update_fields=['status'])
        messages.success(request, 'Reserva cancelada.')
        return redirect(reverse('food_service:reserva-list'))


class ReservaAtenderView(PermissaoRequiredMixin, View):
    """Reserva chegou: marca como atendida e leva direto para abrir a comanda da mesa."""

    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk):
        reserva = get_object_or_404(Reserva.objects.for_filial(request.filial_ativa), pk=pk)
        reserva.status = Reserva.Status.ATENDIDA
        reserva.save(update_fields=['status'])
        from urllib.parse import urlencode

        params = {}
        if reserva.mesa_id:
            params['mesa_id'] = reserva.mesa_id
        if reserva.nome_contato:
            params['nome_ocupante'] = reserva.nome_contato
        if reserva.quantidade_pessoas:
            params['quantidade_pessoas'] = reserva.quantidade_pessoas
        destino = reverse('food_service:comanda-abrir')
        if params:
            destino += f'?{urlencode(params)}'
        return redirect(destino)
