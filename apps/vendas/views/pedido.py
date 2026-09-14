"""Views de Pedido de Venda — ciclo completo."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View

from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.vendas.forms import (
    AdicionarItemForm, CancelarPedidoForm, DevolverPedidoForm, PedidoVendaForm,
)
from apps.produtos.models import ProdutoApresentacao
from apps.vendas.models import PedidoVenda
from apps.vendas.services.venda_service import VendaService


class PedidoListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    template_name = 'vendas/pedido/list.html'

    def get(self, request):
        qs = PedidoVenda.objects.for_filial(request.filial_ativa).select_related(
            'cliente', 'representante', 'usuario',
        )
        busca = request.GET.get('q', '').strip()
        status = request.GET.get('status', '')
        if busca:
            qs = qs.filter(
                Q(numero_pedido__icontains=busca)
                | Q(cliente__razao_social__icontains=busca)
                | Q(cliente__nome_fantasia__icontains=busca)
            )
        if status:
            qs = qs.filter(status=status)

        qs = qs.order_by('-data_emissao')
        page_obj = Paginator(qs, 25).get_page(request.GET.get('page'))
        return render(request, self.template_name, {
            'page_obj': page_obj,
            'pedidos': page_obj.object_list,
            'busca': busca,
            'status': status,
            'status_choices': PedidoVenda.Status.choices,
        })


class PedidoCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'criar'
    template_name = 'vendas/pedido/criar.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form': PedidoVendaForm(filial=request.filial_ativa),
            'title': 'Novo Pedido de Venda',
            'cancel_url': reverse_lazy('vendas:pedido-list'),
        })

    def post(self, request):
        form = PedidoVendaForm(request.POST, filial=request.filial_ativa)
        if form.is_valid():
            try:
                pedido = VendaService.criar_pedido(
                    filial=request.filial_ativa,
                    usuario=request.user,
                    cliente=form.cleaned_data['cliente'],
                    tipo=form.cleaned_data['tipo'],
                    representante=form.cleaned_data.get('representante'),
                    tabela_preco=form.cleaned_data.get('tabela_preco'),
                    observacao=form.cleaned_data.get('observacao', ''),
                )
                # Atualiza os demais campos do form
                for campo in (
                    'transportadora', 'modalidade_frete', 'valor_frete',
                    'data_entrega_prevista', 'prioridade', 'origem',
                    'observacao_interna',
                ):
                    setattr(pedido, campo, form.cleaned_data.get(campo))
                pedido.save()

                messages.success(request, f'Pedido {pedido.numero_pedido} criado. Adicione os itens.')
                return redirect('vendas:pedido-detail', pk=pedido.pk)
            except DomainError as e:
                messages.error(request, str(e))
        return render(request, self.template_name, {
            'form': form,
            'title': 'Novo Pedido de Venda',
            'cancel_url': reverse_lazy('vendas:pedido-list'),
        })


class PedidoDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    template_name = 'vendas/pedido/detail.html'

    def get(self, request, pk):
        pedido = get_object_or_404(
            PedidoVenda.objects.for_filial(request.filial_ativa)
            .select_related('cliente', 'representante', 'tabela_preco', 'usuario'),
            pk=pk,
        )
        itens = pedido.itens.select_related('produto', 'produto__unidade_medida').all()
        apresentacoes_por_produto = {}
        if pedido.status == 'faturado':
            apresentacoes = ProdutoApresentacao.objects.ativas().filter(
                produto_id__in=[item.produto_id for item in itens], permite_venda=True,
            ).select_related('unidade').order_by('produto_id', 'fator_conversao')
            for apresentacao in apresentacoes:
                apresentacoes_por_produto.setdefault(apresentacao.produto_id, []).append(apresentacao)
        return render(request, self.template_name, {
            'pedido': pedido,
            'itens': itens,
            'apresentacoes_por_produto': apresentacoes_por_produto,
            'adicionar_item_form': AdicionarItemForm(filial=request.filial_ativa) if pedido.status == 'rascunho' else None,
            'cancelar_form': CancelarPedidoForm() if pedido.pode_cancelar else None,
            'devolver_form': DevolverPedidoForm() if pedido.status == 'faturado' else None,
        })


class PedidoEditarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'editar'
    template_name = 'vendas/pedido/criar.html'

    def get(self, request, pk):
        pedido = get_object_or_404(PedidoVenda.objects.for_filial(request.filial_ativa), pk=pk)
        if pedido.status != PedidoVenda.Status.RASCUNHO:
            messages.error(request, 'Apenas pedidos em rascunho podem ser editados.')
            return redirect('vendas:pedido-detail', pk=pk)
        return render(request, self.template_name, {
            'form': PedidoVendaForm(instance=pedido, filial=request.filial_ativa),
            'pedido': pedido,
            'title': f'Editar Pedido {pedido.numero_pedido}',
            'cancel_url': reverse_lazy('vendas:pedido-detail', kwargs={'pk': pk}),
        })

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoVenda.objects.for_filial(request.filial_ativa), pk=pk)
        form = PedidoVendaForm(request.POST, instance=pedido, filial=request.filial_ativa)
        if form.is_valid():
            form.save()
            messages.success(request, 'Pedido atualizado.')
            return redirect('vendas:pedido-detail', pk=pk)
        return render(request, self.template_name, {
            'form': form,
            'pedido': pedido,
            'title': f'Editar Pedido {pedido.numero_pedido}',
            'cancel_url': reverse_lazy('vendas:pedido-detail', kwargs={'pk': pk}),
        })


class AdicionarItemView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoVenda.objects.for_filial(request.filial_ativa), pk=pk)
        form = AdicionarItemForm(request.POST, filial=request.filial_ativa)
        if form.is_valid():
            try:
                VendaService.adicionar_item(
                    pedido=pedido,
                    produto=form.cleaned_data['produto'],
                    quantidade=form.cleaned_data['quantidade'],
                    valor_unitario=form.cleaned_data.get('valor_unitario'),
                    percentual_desconto=form.cleaned_data.get('percentual_desconto') or 0,
                )
                messages.success(request, 'Item adicionado.')
            except DomainError as e:
                messages.error(request, str(e))
        else:
            messages.error(request, 'Preencha corretamente os dados do item.')
        return redirect('vendas:pedido-detail', pk=pk)


class RemoverItemView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'editar'

    def post(self, request, pk, item_id):
        pedido = get_object_or_404(PedidoVenda.objects.for_filial(request.filial_ativa), pk=pk)
        try:
            VendaService.remover_item(pedido, item_id)
            messages.success(request, 'Item removido.')
        except DomainError as e:
            messages.error(request, str(e))
        return redirect('vendas:pedido-detail', pk=pk)


class ConfirmarPedidoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'aprovar'

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoVenda.objects.for_filial(request.filial_ativa), pk=pk)
        try:
            VendaService.confirmar_pedido(pedido, request.user)
            messages.success(request, f'Pedido {pedido.numero_pedido} confirmado e estoque reservado.')
        except DomainError as e:
            messages.error(request, str(e))
        return redirect('vendas:pedido-detail', pk=pk)


class SepararPedidoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoVenda.objects.for_filial(request.filial_ativa), pk=pk)
        try:
            separacao = VendaService.separar_pedido(pedido, request.user)
            messages.success(request, f'Separação {separacao.numero} concluída.')
        except DomainError as e:
            messages.error(request, str(e))
        return redirect('vendas:pedido-detail', pk=pk)


class FaturarPedidoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoVenda.objects.for_filial(request.filial_ativa), pk=pk)
        try:
            VendaService.faturar_pedido(pedido, request.user)
            messages.success(request, f'Pedido {pedido.numero_pedido} faturado.')
        except DomainError as e:
            messages.error(request, str(e))
        return redirect('vendas:pedido-detail', pk=pk)


class CancelarPedidoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'cancelar'

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoVenda.objects.for_filial(request.filial_ativa), pk=pk)
        form = CancelarPedidoForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Informe o motivo do cancelamento.')
            return redirect('vendas:pedido-detail', pk=pk)
        try:
            VendaService.cancelar_pedido(pedido, request.user, form.cleaned_data['motivo'])
            messages.success(request, f'Pedido {pedido.numero_pedido} cancelado.')
        except DomainError as e:
            messages.error(request, str(e))
        return redirect('vendas:pedido-detail', pk=pk)


class DevolverPedidoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoVenda.objects.for_filial(request.filial_ativa), pk=pk)
        form = DevolverPedidoForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Preencha o motivo da devolução.')
            return redirect('vendas:pedido-detail', pk=pk)

        # Coleta itens devolvidos do POST
        itens_devolvidos = []
        for item in pedido.itens.all():
            qtd_str = request.POST.get(f'item_{item.pk}_qtd', '0').replace(',', '.')
            try:
                qtd = float(qtd_str)
            except ValueError:
                qtd = 0
            if qtd > 0:
                retornar = request.POST.get(f'item_{item.pk}_retornar') == 'on'
                apresentacao_id = request.POST.get(f'item_{item.pk}_apresentacao', '').strip()
                apresentacao = (
                    ProdutoApresentacao.objects.filter(pk=apresentacao_id).first()
                    if apresentacao_id else None
                )
                itens_devolvidos.append({
                    'item_pedido_id': item.pk,
                    'quantidade': qtd,
                    'retornar_ao_estoque': retornar,
                    'apresentacao': apresentacao,
                })

        if not itens_devolvidos:
            messages.error(request, 'Selecione ao menos um item com quantidade > 0.')
            return redirect('vendas:pedido-detail', pk=pk)

        try:
            dev = VendaService.criar_devolucao(
                pedido=pedido,
                usuario=request.user,
                motivo=form.cleaned_data['motivo'],
                descricao=form.cleaned_data.get('descricao', ''),
                itens_devolvidos=itens_devolvidos,
            )
            messages.success(request, f'Devolução {dev.numero} registrada. Valor: R$ {dev.valor_total}.')
        except DomainError as e:
            messages.error(request, str(e))
        return redirect('vendas:pedido-detail', pk=pk)
