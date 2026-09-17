"""CRUD de Categoria de Produto."""
from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.services.request_scope import empresa_operacional
from apps.produtos.forms import CategoriaProdutoForm
from apps.produtos.models import CategoriaProduto
from apps.produtos.services.replicacao_service import ReplicacaoProdutoService


def _sincronizar_categoria_sem_quebrar(request, categoria):
    try:
        ReplicacaoProdutoService._vincular_categoria(categoria, categoria.filial)
        ReplicacaoProdutoService.sincronizar_categoria(categoria)
    except Exception:
        messages.warning(
            request,
            'Cadastro salvo, mas nao foi possivel replicar para outras filiais agora.',
        )


def _duplicidade_categoria(form, empresa, filial, categoria_pai, instance=None):
    nome = form.cleaned_data.get('nome', '').strip()
    if not nome:
        return False
    qs = CategoriaProduto.objects.for_filial(filial).filter(
        empresa=empresa,
        categoria_pai=categoria_pai,
        nome__iexact=nome,
    )
    if instance and instance.pk:
        qs = qs.exclude(pk=instance.pk)
    if qs.exists():
        campo = 'nome'
        form.add_error(campo, 'Ja existe uma categoria/subcategoria com esse nome nesta filial.')
        return True
    return False


class CategoriaListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    template_name = 'produtos/categoria/list.html'

    def get(self, request):
        empresa = empresa_operacional(request)
        base_qs = CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
            empresa=empresa,
        )
        busca = request.GET.get('q', '').strip()
        tipo = request.GET.get('tipo', 'categorias')

        categorias_qs = base_qs.filter(categoria_pai__isnull=True)
        subcategorias_qs = base_qs.filter(categoria_pai__isnull=False).select_related('categoria_pai')

        if busca:
            categorias_qs = categorias_qs.filter(nome__icontains=busca)
            subcategorias_qs = subcategorias_qs.filter(
                Q(nome__icontains=busca) | Q(categoria_pai__nome__icontains=busca),
            )

        subcategorias = subcategorias_qs.order_by('categoria_pai__nome', 'nome')
        subcategorias_prefetch = CategoriaProduto.objects.none()

        if tipo == 'subcategorias':
            categorias_qs = categorias_qs.filter(subcategorias__in=subcategorias_qs).distinct()

        categorias_tree = categorias_qs.prefetch_related(
            Prefetch('subcategorias', queryset=subcategorias_prefetch, to_attr='subcategorias_listagem'),
        ).order_by('nome')

        return render(request, self.template_name, {
            'categorias_tree': categorias_tree,
            'subcategorias': subcategorias,
            'busca': busca,
            'tipo': tipo,
            'total_categorias': base_qs.filter(categoria_pai__isnull=True).count(),
            'total_subcategorias': base_qs.filter(categoria_pai__isnull=False).count(),
        })


class CategoriaCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'criar'
    template_name = 'produtos/categoria/form.html'

    def get(self, request):
        tipo = request.GET.get('tipo', 'categoria')
        empresa = empresa_operacional(request)
        initial = {}
        if tipo == 'subcategoria':
            primeira_categoria = CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                empresa=empresa,
                ativo=True, categoria_pai__isnull=True,
            ).order_by('nome').first()
            if primeira_categoria:
                initial['categoria_pai'] = primeira_categoria
        return render(request, self.template_name, {
            'form': CategoriaProdutoForm(
                empresa=empresa,
                filial=request.filial_ativa,
                initial=initial,
                modo=tipo,
            ),
            'title': 'Nova Subcategoria' if tipo == 'subcategoria' else 'Nova Categoria',
            'cancel_url': f'{reverse_lazy("produtos:categoria-list")}?tipo={tipo}s' if tipo in ('categoria', 'subcategoria') else reverse_lazy('produtos:categoria-list'),
        })

    def post(self, request):
        tipo = request.GET.get('tipo', 'categoria')
        empresa = empresa_operacional(request)
        form = CategoriaProdutoForm(
            request.POST,
            empresa=empresa,
            filial=request.filial_ativa,
            modo=tipo,
        )
        if form.is_valid():
            categoria_pai = None if tipo == 'categoria' else form.cleaned_data.get('categoria_pai')
            if _duplicidade_categoria(form, empresa, request.filial_ativa, categoria_pai):
                return render(request, self.template_name, {
                    'form': form,
                    'title': 'Nova Subcategoria' if tipo == 'subcategoria' else 'Nova Categoria',
                    'cancel_url': f'{reverse_lazy("produtos:categoria-list")}?tipo={tipo}s' if tipo in ('categoria', 'subcategoria') else reverse_lazy('produtos:categoria-list'),
                })
            obj = form.save(commit=False)
            obj.empresa = empresa
            obj.filial = request.filial_ativa
            if tipo == 'categoria':
                obj.categoria_pai = None
            try:
                obj.save()
            except IntegrityError:
                form.add_error('nome', 'Ja existe uma categoria/subcategoria com esse nome nesta filial.')
                return render(request, self.template_name, {
                    'form': form,
                    'title': 'Nova Subcategoria' if tipo == 'subcategoria' else 'Nova Categoria',
                    'cancel_url': f'{reverse_lazy("produtos:categoria-list")}?tipo={tipo}s' if tipo in ('categoria', 'subcategoria') else reverse_lazy('produtos:categoria-list'),
                })
            _sincronizar_categoria_sem_quebrar(request, obj)
            label = 'Subcategoria' if obj.categoria_pai_id else 'Categoria'
            messages.success(request, f'{label} "{obj}" criada.')
            return redirect(f'{reverse_lazy("produtos:categoria-list")}?tipo={tipo}s')
        return render(request, self.template_name, {
            'form': form,
            'title': 'Nova Subcategoria' if tipo == 'subcategoria' else 'Nova Categoria',
            'cancel_url': f'{reverse_lazy("produtos:categoria-list")}?tipo={tipo}s' if tipo in ('categoria', 'subcategoria') else reverse_lazy('produtos:categoria-list'),
        })


class CategoriaUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'editar'
    template_name = 'produtos/categoria/form.html'

    def get(self, request, pk):
        empresa = empresa_operacional(request)
        obj = get_object_or_404(
            CategoriaProduto.objects.for_filial(request.filial_ativa), pk=pk, empresa=empresa,
        )
        modo = 'subcategoria' if obj.categoria_pai_id else 'categoria'
        return render(request, self.template_name, {
            'form': CategoriaProdutoForm(
                instance=obj,
                empresa=empresa,
                filial=request.filial_ativa,
                modo=modo,
            ),
            'title': f'Editar — {obj}',
            'cancel_url': f'{reverse_lazy("produtos:categoria-list")}?tipo={modo}s',
        })

    def post(self, request, pk):
        empresa = empresa_operacional(request)
        obj = get_object_or_404(
            CategoriaProduto.objects.for_filial(request.filial_ativa), pk=pk, empresa=empresa,
        )
        modo = 'subcategoria' if obj.categoria_pai_id else 'categoria'
        form = CategoriaProdutoForm(
            request.POST,
            instance=obj,
            empresa=empresa,
            filial=request.filial_ativa,
            modo=modo,
        )
        if form.is_valid():
            categoria_pai = None if modo == 'categoria' else form.cleaned_data.get('categoria_pai')
            if _duplicidade_categoria(form, empresa, request.filial_ativa, categoria_pai, obj):
                return render(request, self.template_name, {
                    'form': form,
                    'title': f'Editar â€” {obj}',
                    'cancel_url': f'{reverse_lazy("produtos:categoria-list")}?tipo={modo}s',
                })
            categoria = form.save(commit=False)
            if modo == 'categoria':
                categoria.categoria_pai = None
            categoria.filial = request.filial_ativa
            try:
                categoria.save()
            except IntegrityError:
                form.add_error('nome', 'Ja existe uma categoria/subcategoria com esse nome nesta filial.')
                return render(request, self.template_name, {
                    'form': form,
                    'title': f'Editar â€” {obj}',
                    'cancel_url': f'{reverse_lazy("produtos:categoria-list")}?tipo={modo}s',
                })
            _sincronizar_categoria_sem_quebrar(request, categoria)
            messages.success(request, 'Categoria atualizada.')
            return redirect(f'{reverse_lazy("produtos:categoria-list")}?tipo={modo}s')
        return render(request, self.template_name, {
            'form': form,
            'title': f'Editar — {obj}',
            'cancel_url': f'{reverse_lazy("produtos:categoria-list")}?tipo={modo}s',
        })
