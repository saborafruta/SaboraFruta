"""Abertura, lançamento de itens, divisão e fechamento de comandas."""
import json
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.decorators.http import require_POST

from apps.cadastros.models import Cliente
from apps.core.services.exceptions import DadosInvalidosError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.financeiro.models import FormaPagamento
from apps.produtos.models import Produto

from ..models import Comanda, ItemComanda, Mesa
from ..services import ComandaService


class ComandaAbrirView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'criar'

    def get(self, request):
        mesa_ids = request.GET.getlist('mesa_id')
        mesas = list(Mesa.objects.for_filial(request.filial_ativa).filter(pk__in=mesa_ids)) if mesa_ids else []
        return render(request, 'food_service/comanda_abrir.html', {
            'title': 'Abrir comanda',
            'mesas_selecionadas': mesas,
            'nome_ocupante_sugerido': request.GET.get('nome_ocupante', ''),
            'quantidade_pessoas_sugerida': request.GET.get('quantidade_pessoas', '1'),
        })

    def post(self, request):
        mesa_ids = request.POST.getlist('mesa_id')
        mesas = list(Mesa.objects.for_filial(request.filial_ativa).filter(pk__in=mesa_ids)) if mesa_ids else []

        cliente = None
        cliente_id = request.POST.get('cliente_id')
        if cliente_id:
            cliente = Cliente.objects.for_filial(request.filial_ativa).filter(pk=cliente_id).first()

        quantidade_pessoas = request.POST.get('quantidade_pessoas', '1').strip()
        quantidade_pessoas = int(quantidade_pessoas) if quantidade_pessoas.isdigit() else 1

        comanda = ComandaService.abrir(
            filial=request.filial_ativa,
            usuario=request.user,
            mesas=mesas,
            cliente=cliente,
            nome_ocupante=request.POST.get('nome_ocupante', '').strip(),
            garcom=request.user,
            quantidade_pessoas=quantidade_pessoas,
        )
        return redirect(reverse('food_service:comanda-detail', args=[comanda.pk]))


class ComandaDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'ver'

    def get(self, request, pk):
        comanda = get_object_or_404(
            Comanda.objects.for_filial(request.filial_ativa).prefetch_related('itens__produto', 'mesas'),
            pk=pk,
        )
        outras_comandas_abertas = (
            Comanda.objects.for_filial(request.filial_ativa)
            .filter(status=Comanda.Status.ABERTA)
            .exclude(pk=comanda.pk)
        )
        mesas_livres = Mesa.objects.for_filial(request.filial_ativa).filter(
            ativo=True, status=Mesa.Status.LIVRE,
        )
        try:
            formas_pagamento = list(
                FormaPagamento.objects.filter(
                    empresa=request.filial_ativa.empresa, ativo=True,
                ).values('id', 'descricao', 'tipo')
            )
        except Exception:
            formas_pagamento = []
        return render(request, 'food_service/comanda_detail.html', {
            'title': f'Comanda #{comanda.pk}',
            'comanda': comanda,
            'produtos': Produto.objects.for_filial(request.filial_ativa).filter(ativo=True).order_by('descricao')[:500],
            'outras_comandas_abertas': outras_comandas_abertas,
            'mesas_livres': mesas_livres,
            'formas_pagamento': formas_pagamento,
        })


def _comanda_da_filial(request, pk):
    return get_object_or_404(Comanda.objects.for_filial(request.filial_ativa), pk=pk)


class ComandaAdicionarItemView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk):
        comanda = _comanda_da_filial(request, pk)
        produto = get_object_or_404(
            Produto.objects.for_filial(request.filial_ativa), pk=request.POST.get('produto_id'),
        )
        try:
            ComandaService.adicionar_item(
                comanda=comanda,
                produto=produto,
                quantidade=request.POST.get('quantidade', '1').replace(',', '.'),
                observacoes=request.POST.get('observacoes', '').strip(),
            )
        except DadosInvalidosError as exc:
            messages.error(request, str(exc))
        return redirect(reverse('food_service:comanda-detail', args=[comanda.pk]))


class ComandaRemoverItemView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk, item_pk):
        comanda = _comanda_da_filial(request, pk)
        item = get_object_or_404(ItemComanda, pk=item_pk, comanda=comanda)
        try:
            ComandaService.remover_item(item=item)
        except DadosInvalidosError as exc:
            messages.error(request, str(exc))
        return redirect(reverse('food_service:comanda-detail', args=[comanda.pk]))


class ComandaTransferirItemView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk, item_pk):
        comanda = _comanda_da_filial(request, pk)
        item = get_object_or_404(ItemComanda, pk=item_pk, comanda=comanda)
        destino = _comanda_da_filial(request, request.POST.get('destino_comanda_id'))
        try:
            ComandaService.transferir_item(item=item, destino=destino)
        except DadosInvalidosError as exc:
            messages.error(request, str(exc))
        return redirect(reverse('food_service:comanda-detail', args=[comanda.pk]))


class ComandaUnirView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk):
        comanda = _comanda_da_filial(request, pk)
        origem = _comanda_da_filial(request, request.POST.get('origem_comanda_id'))
        try:
            ComandaService.unir_comandas(origem=origem, destino=comanda)
        except DadosInvalidosError as exc:
            messages.error(request, str(exc))
        return redirect(reverse('food_service:comanda-detail', args=[comanda.pk]))


class ComandaTransferirMesaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk):
        comanda = _comanda_da_filial(request, pk)
        mesa_origem = get_object_or_404(
            Mesa.objects.for_filial(request.filial_ativa), pk=request.POST.get('mesa_origem_id'),
        )
        mesa_destino = get_object_or_404(
            Mesa.objects.for_filial(request.filial_ativa), pk=request.POST.get('mesa_destino_id'),
        )
        ComandaService.transferir_mesa(comanda=comanda, mesa_origem=mesa_origem, mesa_destino=mesa_destino)
        return redirect(reverse('food_service:comanda-detail', args=[comanda.pk]))


class ComandaUnirMesasView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk):
        comanda = _comanda_da_filial(request, pk)
        mesa_ids = request.POST.getlist('mesa_id')
        mesas = list(Mesa.objects.for_filial(request.filial_ativa).filter(pk__in=mesa_ids))
        ComandaService.unir_mesas(comanda=comanda, mesas_adicionais=mesas)
        return redirect(reverse('food_service:comanda-detail', args=[comanda.pk]))


class ComandaLiberarMesaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk):
        comanda = _comanda_da_filial(request, pk)
        mesa = get_object_or_404(
            Mesa.objects.for_filial(request.filial_ativa), pk=request.POST.get('mesa_id'),
        )
        try:
            ComandaService.liberar_mesa(comanda=comanda, mesa=mesa)
        except DadosInvalidosError as exc:
            messages.error(request, str(exc))
        return redirect(reverse('food_service:comanda-detail', args=[comanda.pk]))


class ComandaFecharView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk):
        comanda = _comanda_da_filial(request, pk)
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, TypeError):
            body = request.POST

        pagamentos = body.get('pagamentos', [])
        desconto = Decimal(str(body.get('desconto', '0') or '0'))
        acrescimo = Decimal(str(body.get('acrescimo', '0') or '0'))

        try:
            venda = ComandaService.fechar(
                comanda=comanda,
                request=request,
                pagamentos=pagamentos,
                desconto=desconto,
                acrescimo=acrescimo,
            )
        except DadosInvalidosError as exc:
            return _erro_json_ou_redirect(request, comanda, str(exc))

        if request.headers.get('Accept', '').startswith('application/json') or request.content_type == 'application/json':
            from django.http import JsonResponse
            return JsonResponse({'ok': True, 'venda_id': venda.pk, 'numero_venda': venda.numero_venda})

        messages.success(request, f'Comanda fechada — venda #{venda.numero_venda} gerada.')
        return redirect(reverse('food_service:painel'))


def _erro_json_ou_redirect(request, comanda, erro):
    if request.headers.get('Accept', '').startswith('application/json') or request.content_type == 'application/json':
        from django.http import JsonResponse
        return JsonResponse({'erro': erro}, status=400)
    messages.error(request, erro)
    return redirect(reverse('food_service:comanda-detail', args=[comanda.pk]))


class ComandaHistoricoListView(PermissaoRequiredMixin, View):
    """Histórico de pedidos/atendimentos: comandas já fechadas ou canceladas."""

    permissao_modulo = 'food_service'
    permissao_acao = 'ver'

    def get(self, request):
        qs = (
            Comanda.objects.for_filial(request.filial_ativa)
            .exclude(status=Comanda.Status.ABERTA)
            .select_related('cliente', 'garcom', 'venda_pdv')
            .prefetch_related('itens', 'mesas')
            .order_by('-aberta_em')
        )

        mesa_id = request.GET.get('mesa_id', '').strip()
        mesa_selecionada = None
        if mesa_id.isdigit():
            qs = qs.filter(mesas__pk=mesa_id)
            mesa_selecionada = Mesa.objects.for_filial(request.filial_ativa).filter(pk=mesa_id).first()

        garcom_id = request.GET.get('garcom_id', '').strip()
        if garcom_id.isdigit():
            qs = qs.filter(garcom_id=garcom_id)

        data_inicio = request.GET.get('data_inicio', '').strip()
        data_fim = request.GET.get('data_fim', '').strip()
        if data_inicio:
            qs = qs.filter(aberta_em__date__gte=data_inicio)
        if data_fim:
            qs = qs.filter(aberta_em__date__lte=data_fim)

        paginator = Paginator(qs, 30)
        pagina = paginator.get_page(request.GET.get('pagina'))

        return render(request, 'food_service/comanda_historico.html', {
            'title': 'Histórico de Pedidos e Atendimentos',
            'pagina': pagina,
            'mesa_selecionada': mesa_selecionada,
            'mesas': Mesa.objects.for_filial(request.filial_ativa).order_by('numero'),
        })
