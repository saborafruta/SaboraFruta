"""CRUD de Tabela de Preço e itens."""
import json

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.produtos.forms import TabelaPrecoForm
from apps.produtos.forms.tabela_preco import ItemTabelaPrecoForm
from apps.produtos.models import TabelaPreco, Produto
from apps.produtos.models.tabela_preco import ItemTabelaPreco
from apps.produtos.services.replicacao_service import ReplicacaoProdutoService


def _criar_itens_staged(request, tabela) -> int:
    """
    Cria os itens (produtos) enviados pela tela de criação da tabela via
    campo oculto ``itens_json`` (lista de {produto_id, preco_unitario,
    desconto_maximo, quantidade_minima}). Retorna quantos foram criados.
    """
    raw = request.POST.get('itens_json', '').strip()
    if not raw:
        return 0
    try:
        itens = json.loads(raw)
    except (ValueError, TypeError):
        return 0
    if not isinstance(itens, list):
        return 0

    from decimal import Decimal, InvalidOperation

    def _dec(v, default='0'):
        try:
            return Decimal(str(v if v not in (None, '') else default))
        except (InvalidOperation, ValueError, TypeError):
            return Decimal(default)

    criados = 0
    for it in itens:
        if not isinstance(it, dict):
            continue
        produto_id = it.get('produto_id') or it.get('produto')
        if not produto_id:
            continue
        produto = Produto.objects.for_filial(request.filial_ativa).filter(pk=produto_id).first()
        if not produto:
            continue
        qtd_min = _dec(it.get('quantidade_minima'), '0')
        _, criado = ItemTabelaPreco.objects.get_or_create(
            tabela=tabela,
            produto=produto,
            quantidade_minima=qtd_min,
            defaults={
                'preco_unitario': _dec(it.get('preco_unitario'), '0'),
                'desconto_maximo': _dec(it.get('desconto_maximo'), '0'),
                'desconto_valor': _dec(it.get('desconto_valor'), '0'),
            },
        )
        if criado:
            criados += 1
    return criados


class TabelaPrecoListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    template_name = 'produtos/tabela_preco/list.html'

    def get(self, request):
        qs = TabelaPreco.objects.for_filial(request.filial_ativa)
        q = request.GET.get('q', '').strip()
        tipo = request.GET.get('tipo', '')
        status = request.GET.get('status', 'ativo')

        if q:
            qs = qs.filter(descricao__icontains=q)
        if tipo:
            qs = qs.filter(tipo=tipo)
        if status == 'ativo':
            qs = qs.filter(ativo=True)
        elif status == 'inativo':
            qs = qs.filter(ativo=False)

        page_obj = Paginator(qs.order_by('descricao'), 25).get_page(request.GET.get('page'))
        return render(request, self.template_name, {
            'page_obj': page_obj,
            'tabelas': page_obj.object_list,
            'q': q,
            'tipo': tipo,
            'status': status,
            'tipos': TabelaPreco.Tipo.choices,
        })


class TabelaPrecoCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'criar'
    template_name = 'produtos/tabela_preco/form.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form': TabelaPrecoForm(),
            'title': 'Nova Tabela de Preço',
            'cancel_url': reverse_lazy('produtos:tabela-list'),
        })

    def post(self, request):
        form = TabelaPrecoForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.filial = request.filial_ativa
            obj.save()
            ReplicacaoProdutoService._vincular_tabela_preco(obj, obj.filial)
            ReplicacaoProdutoService.sincronizar_tabela_preco(obj)
            n_itens = _criar_itens_staged(request, obj)
            if n_itens:
                messages.success(request, f'Tabela criada com {n_itens} produto(s).')
            else:
                messages.success(request, 'Tabela de preço criada com sucesso.')
            return redirect('produtos:tabela-update', pk=obj.pk)
        return render(request, self.template_name, {
            'form': form,
            'title': 'Nova Tabela de Preço',
            'cancel_url': reverse_lazy('produtos:tabela-list'),
        })


class TabelaPrecoUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'editar'
    template_name = 'produtos/tabela_preco/form.html'

    def _get_context(self, request, obj):
        itens = obj.itens.select_related('produto').order_by('produto__descricao', 'quantidade_minima')
        q_produto = request.GET.get('q_produto', '').strip()
        produtos_busca = []
        if q_produto:
            produtos_busca = (
                Produto.objects
                .for_filial(request.filial_ativa)
                .filter(ativo=True)
                .filter(Q(descricao__icontains=q_produto) | Q(codigo__icontains=q_produto))
                .exclude(pk__in=itens.values('produto_id').filter(quantidade_minima=0))
                [:20]
            )
        return {
            'form': TabelaPrecoForm(instance=obj),
            'tabela': obj,
            'title': f'Editar — {obj}',
            'cancel_url': reverse_lazy('produtos:tabela-list'),
            'itens': itens,
            'item_form': ItemTabelaPrecoForm(),
            'q_produto': q_produto,
            'produtos_busca': produtos_busca,
        }

    def get(self, request, pk):
        obj = get_object_or_404(TabelaPreco.objects.for_filial(request.filial_ativa), pk=pk)
        return render(request, self.template_name, self._get_context(request, obj))

    def post(self, request, pk):
        obj = get_object_or_404(TabelaPreco.objects.for_filial(request.filial_ativa), pk=pk)
        form = TabelaPrecoForm(request.POST, instance=obj)
        if form.is_valid():
            tabela = form.save()
            ReplicacaoProdutoService._vincular_tabela_preco(tabela, tabela.filial)
            ReplicacaoProdutoService.sincronizar_tabela_preco(tabela)
            messages.success(request, 'Tabela de preço atualizada.')
            return redirect('produtos:tabela-update', pk=pk)
        ctx = self._get_context(request, obj)
        ctx['form'] = form
        return render(request, self.template_name, ctx)


class TabelaPrecoToggleAtivoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'editar'

    def post(self, request, pk):
        obj = get_object_or_404(TabelaPreco.objects.for_filial(request.filial_ativa), pk=pk)
        obj.ativo = not obj.ativo
        obj.save(update_fields=['ativo'])
        estado = 'ativada' if obj.ativo else 'desativada'
        messages.success(request, f"Tabela '{obj.descricao}' {estado}.")
        return redirect('produtos:tabela-list')


class ItemTabelaPrecoCreateView(PermissaoRequiredMixin, View):
    """Adiciona produto à tabela de preço."""
    permissao_modulo = 'produtos'
    permissao_acao = 'editar'

    def post(self, request, tabela_pk):
        tabela = get_object_or_404(TabelaPreco.objects.for_filial(request.filial_ativa), pk=tabela_pk)
        produto_id = request.POST.get('produto')
        preco_unitario = request.POST.get('preco_unitario')
        desconto_maximo = request.POST.get('desconto_maximo') or 0
        desconto_valor = request.POST.get('desconto_valor') or 0
        quantidade_minima = request.POST.get('quantidade_minima') or 0

        if not produto_id or not preco_unitario:
            messages.error(request, 'Produto e preço são obrigatórios.')
            return redirect('produtos:tabela-update', pk=tabela_pk)

        produto = get_object_or_404(
            Produto.objects.for_filial(request.filial_ativa), pk=produto_id,
        )
        nome_prod = produto.descricao_pdv or produto.descricao
        item, criado = ItemTabelaPreco.objects.get_or_create(
            tabela=tabela,
            produto=produto,
            quantidade_minima=quantidade_minima,
            defaults={
                'preco_unitario': preco_unitario,
                'desconto_maximo': desconto_maximo,
                'desconto_valor': desconto_valor,
            }
        )
        if not criado:
            item.preco_unitario = preco_unitario
            item.desconto_maximo = desconto_maximo
            item.desconto_valor = desconto_valor
            item.save(update_fields=['preco_unitario', 'desconto_maximo', 'desconto_valor'])
            messages.success(request, f'Preço de "{nome_prod}" atualizado.')
        else:
            messages.success(request, f'"{nome_prod}" adicionado à tabela.')
        return redirect('produtos:tabela-update', pk=tabela_pk)


class ItemTabelaPrecoDeleteView(PermissaoRequiredMixin, View):
    """Remove item da tabela de preço."""
    permissao_modulo = 'produtos'
    permissao_acao = 'editar'

    def post(self, request, tabela_pk, item_pk):
        tabela = get_object_or_404(TabelaPreco.objects.for_filial(request.filial_ativa), pk=tabela_pk)
        item = get_object_or_404(ItemTabelaPreco, pk=item_pk, tabela=tabela)
        nome = item.produto.descricao_pdv or item.produto.descricao
        item.delete()
        messages.success(request, f'"{nome}" removido da tabela.')
        return redirect('produtos:tabela-update', pk=tabela_pk)


class ProdutoSearchParaTabelaView(PermissaoRequiredMixin, View):
    """Busca AJAX de produtos para adicionar na tabela."""
    permissao_modulo = 'produtos'

    def get(self, request, tabela_pk):
        q = request.GET.get('q', '').strip()
        tabela = get_object_or_404(TabelaPreco.objects.for_filial(request.filial_ativa), pk=tabela_pk)
        resultados = []
        if len(q) >= 2:
            produtos = (
                Produto.objects
                .for_filial(request.filial_ativa)
                .filter(ativo=True)
                .filter(
                    Q(descricao__icontains=q) | Q(descricao_pdv__icontains=q)
                    | Q(codigo__icontains=q) | Q(codigo_barras__icontains=q)
                )
                [:15]
            )
            for p in produtos:
                item_existente = ItemTabelaPreco.objects.filter(tabela=tabela, produto=p, quantidade_minima=0).first()
                resultados.append({
                    'id': p.pk,
                    'nome': p.descricao_pdv or p.descricao,
                    'codigo': p.codigo or '',
                    'preco_atual': str(item_existente.preco_unitario) if item_existente else '',
                    'ja_na_tabela': item_existente is not None,
                })
        return JsonResponse({'resultados': resultados})


class ProdutoSearchTabelaNovaView(PermissaoRequiredMixin, View):
    """Busca AJAX de produtos para a tela de CRIAÇÃO da tabela (sem tabela
    existente ainda). Sugere o preço de venda do produto como valor inicial."""
    permissao_modulo = 'produtos'

    def get(self, request):
        q = request.GET.get('q', '').strip()
        resultados = []
        if len(q) >= 2:
            produtos = (
                Produto.objects
                .for_filial(request.filial_ativa)
                .filter(ativo=True)
                .filter(
                    Q(descricao__icontains=q) | Q(descricao_pdv__icontains=q)
                    | Q(codigo__icontains=q) | Q(codigo_barras__icontains=q)
                )
                [:15]
            )
            for p in produtos:
                resultados.append({
                    'id': p.pk,
                    'nome': p.descricao_pdv or p.descricao,
                    'codigo': p.codigo or '',
                    'preco_atual': str(p.preco_venda or ''),
                    'ja_na_tabela': False,
                })
        return JsonResponse({'resultados': resultados})
