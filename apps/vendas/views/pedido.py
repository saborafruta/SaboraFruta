"""Views de Pedido de Venda — ciclo completo."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View

from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.tenant_context import get_current_database_alias
from apps.financeiro.models import CondicaoPagamento, ContaReceber, FormaPagamento
from apps.vendas.forms import (
    AdicionarItemForm, CancelarPedidoForm, DevolverPedidoForm, PedidoVendaForm,
)
from apps.produtos.models import ProdutoApresentacao
from apps.vendas.models import PedidoVenda
from apps.vendas.services.venda_service import VendaService
from apps.vendas.services.pedido_pdf import (
    PedidoVendaPdfService,
    enviar_pdf_pedido_whatsapp,
    nome_arquivo_pedido,
)


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
            .select_related(
                'cliente', 'representante', 'tabela_preco', 'usuario',
                'forma_pagamento', 'condicao_pagamento', 'endereco_entrega',
            ),
            pk=pk,
        )
        itens = pedido.itens.select_related(
            'produto', 'produto__unidade_medida', 'deposito_separacao',
        ).all()
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
            'pode_aprovar_externo': (
                pedido.origem == PedidoVenda.Origem.EXTERNO
                and pedido.status == PedidoVenda.Status.AGUARDANDO_APROVACAO
                and request.user.tem_permissao('vendas', 'aprovar')
            ),
            'formas_pagamento': FormaPagamento.objects.filter(
                empresa=request.filial_ativa.empresa,
                ativo=True,
            ).filter(
                Q(filial=request.filial_ativa) | Q(filial__isnull=True)
            ).order_by('descricao'),
            'condicoes_pagamento': CondicaoPagamento.objects.filter(
                empresa=request.filial_ativa.empresa,
                ativo=True,
            ).order_by('descricao'),
            'contas_receber': ContaReceber.objects.for_filial(request.filial_ativa).filter(
                documento_tipo='pedido_venda', documento_id=pedido.pk,
            ).order_by('parcela'),
            'ultima_separacao': pedido.separacoes.select_related(
                'usuario_separador',
            ).order_by('-data_inicio').first(),
            'pedido_criado': request.GET.get('criado') == '1',
        })


class PedidoVendaPdfView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'

    def get(self, request, pk):
        pedido = get_object_or_404(
            PedidoVenda.objects.for_filial(request.filial_ativa).select_related(
                'filial', 'cliente', 'usuario', 'forma_pagamento', 'condicao_pagamento',
            ),
            pk=pk,
        )
        resposta = HttpResponse(
            PedidoVendaPdfService.gerar(pedido),
            content_type='application/pdf',
        )
        resposta['Content-Disposition'] = f'attachment; filename="{nome_arquivo_pedido(pedido)}"'
        return resposta


class EnviarPedidoVendaPdfWhatsAppView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'criar'

    def post(self, request, pk):
        pedido = get_object_or_404(
            PedidoVenda.objects.for_filial(request.filial_ativa).select_related(
                'filial', 'cliente', 'usuario', 'forma_pagamento', 'condicao_pagamento',
            ),
            pk=pk,
        )
        enviado, mensagem = enviar_pdf_pedido_whatsapp(
            pedido,
            db_alias=get_current_database_alias(),
        )
        if enviado:
            messages.success(request, mensagem)
        else:
            messages.error(request, mensagem)
        return redirect('vendas:pedido-detail', pk=pedido.pk)


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
            itens_conferidos = None
            if request.POST.get('conferencia_detalhada') == '1':
                itens_conferidos = []
                for item in pedido.itens.order_by('numero_item'):
                    itens_conferidos.append({
                        'item_id': item.pk,
                        'conferido': request.POST.get(f'item_{item.pk}_conferido') == 'on',
                        'quantidade_separada': request.POST.get(
                            f'item_{item.pk}_quantidade', '0',
                        ).replace(',', '.'),
                        'deposito_id': request.POST.get(
                            f'item_{item.pk}_deposito_id',
                        ),
                        'observacao': request.POST.get(f'item_{item.pk}_observacao', ''),
                        'produto_substituto_id': request.POST.get(
                            f'item_{item.pk}_substituto_id',
                        ),
                        'quantidade_substituta': request.POST.get(
                            f'item_{item.pk}_substituto_quantidade', '0',
                        ).replace(',', '.'),
                        'deposito_substituto_id': request.POST.get(
                            f'item_{item.pk}_substituto_deposito_id',
                        ),
                    })
            separacao = VendaService.separar_pedido(
                pedido,
                request.user,
                itens_conferidos=itens_conferidos,
                observacao=request.POST.get('observacao_separacao', ''),
            )
            messages.success(request, f'Separação {separacao.numero} concluída.')
        except DomainError as e:
            messages.error(request, str(e))
        if request.POST.get('origem') == 'fila-separacao':
            return redirect('vendas:pedido-separacao')
        return redirect('vendas:pedido-detail', pk=pk)


class FaturarPedidoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoVenda.objects.for_filial(request.filial_ativa), pk=pk)
        try:
            forma = None
            condicao = None
            pagamento_confirmado = None
            prazo = None
            referencia = ''
            if pedido.origem in PedidoVenda.ORIGENS_FORCA_VENDAS:
                forma = FormaPagamento.objects.filter(
                    pk=request.POST.get('forma_pagamento'),
                    empresa=request.filial_ativa.empresa,
                    ativo=True,
                ).filter(
                    Q(filial=request.filial_ativa) | Q(filial__isnull=True)
                ).first()
                if not forma:
                    raise DomainError('Selecione uma forma de pagamento válida.')
                condicao_id = request.POST.get('condicao_pagamento')
                condicao = CondicaoPagamento.objects.filter(
                    pk=condicao_id,
                    empresa=request.filial_ativa.empresa,
                    ativo=True,
                ).first() if condicao_id else None
                pagamento_confirmado = request.POST.get('pagamento_confirmado') == 'on'
                try:
                    prazo = max(int(request.POST.get('prazo_pagamento_dias') or 0), 0)
                except ValueError as exc:
                    raise DomainError('O prazo de pagamento é inválido.') from exc
                referencia = request.POST.get('referencia_pagamento', '')
            VendaService.faturar_pedido(
                pedido,
                request.user,
                forma_pagamento=forma,
                condicao_pagamento=condicao,
                pagamento_confirmado=pagamento_confirmado,
                prazo_pagamento_dias=prazo,
                referencia_pagamento=referencia,
            )
            messages.success(request, f'Pedido {pedido.numero_pedido} faturado.')
        except DomainError as e:
            messages.error(request, str(e))
        return redirect('vendas:pedido-detail', pk=pk)


class IniciarEntregaPedidoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoVenda.objects.for_filial(request.filial_ativa), pk=pk)
        try:
            VendaService.iniciar_entrega(
                pedido,
                request.user,
                responsavel=request.POST.get('responsavel_entrega', ''),
                observacao=request.POST.get('observacao_entrega', ''),
            )
            messages.success(request, f'Pedido {pedido.numero_pedido} saiu para entrega.')
        except DomainError as exc:
            messages.error(request, str(exc))
        return redirect('vendas:pedido-detail', pk=pk)


class ConcluirEntregaPedidoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'editar'

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoVenda.objects.for_filial(request.filial_ativa), pk=pk)
        try:
            VendaService.concluir_entrega(pedido)
            messages.success(request, f'Entrega do pedido {pedido.numero_pedido} concluída.')
        except DomainError as exc:
            messages.error(request, str(exc))
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
