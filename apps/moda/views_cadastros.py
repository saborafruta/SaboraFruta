"""
Telas de cadastro do vertical Moda: Grades, Cores e Produtos.

São as três que fecham o ciclo até o SKU — grade define os tamanhos, cor
define as cores, e o produto cruza as duas para gerar as variantes.
"""
from django.contrib import messages
from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.services.exceptions import DadosInvalidosError

from .forms import CorForm, GradeForm, ProdutoModaForm
from .models import Cor, Grade, ItemGrade, ProdutoCor, ProdutoModa, Tamanho
from .services import VarianteService
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


# ══════════════════════════════════════════════════════════════════════
# GRADES
# ══════════════════════════════════════════════════════════════════════

class GradeListView(ModaBaseView):
    def get(self, request):
        grades = (
            Grade.objects.for_filial(_filial(request))
            .prefetch_related(Prefetch('itens', queryset=ItemGrade.objects.select_related('tamanho')))
            .annotate(qtd_produtos=Count('produtos', distinct=True))
        )
        return render(request, 'moda/grade_list.html', {
            'title': 'Grades de Tamanho',
            'grades': grades,
        })


class GradeFormView(ModaBaseView):
    permissao_acao = 'criar'

    def _obter(self, request, pk):
        if pk is None:
            return None
        return get_object_or_404(Grade.objects.for_filial(_filial(request)), pk=pk)

    def get(self, request, pk=None):
        grade = self._obter(request, pk)
        filial = _filial(request)
        return render(request, 'moda/grade_form.html', {
            'title': f'Grade {grade.nome}' if grade else 'Nova Grade',
            'grade': grade,
            'form': GradeForm(instance=grade, filial=filial),
            'tamanhos': Tamanho.objects.for_filial(filial).filter(ativo=True),
            'selecionados': (
                list(grade.itens.values_list('tamanho_id', flat=True)) if grade else []
            ),
        })

    def post(self, request, pk=None):
        grade = self._obter(request, pk)
        filial = _filial(request)
        form = GradeForm(request.POST, instance=grade, filial=filial)

        if not form.is_valid():
            return render(request, 'moda/grade_form.html', {
                'title': 'Nova Grade' if grade is None else f'Grade {grade.nome}',
                'grade': grade, 'form': form,
                'tamanhos': Tamanho.objects.for_filial(filial).filter(ativo=True),
                'selecionados': [int(i) for i in request.POST.getlist('tamanho')],
            })

        grade = form.save(commit=False)
        grade.filial = filial
        grade.save()

        # A ordem vem da ordem em que os checkboxes chegaram no POST, que é
        # a ordem da tela — é assim que o usuário monta PP, P, M, G.
        ids = [int(i) for i in request.POST.getlist('tamanho')]
        grade.itens.exclude(tamanho_id__in=ids).delete()
        for posicao, tamanho_id in enumerate(ids, start=1):
            ItemGrade.objects.update_or_create(
                grade=grade, tamanho_id=tamanho_id,
                defaults={'ordem': posicao * 10},
            )

        messages.success(request, f'Grade {grade.nome} salva com {len(ids)} tamanho(s).')
        return redirect(reverse('moda:grade-list'))


# ══════════════════════════════════════════════════════════════════════
# CORES
# ══════════════════════════════════════════════════════════════════════

class CorListView(ModaBaseView):
    def get(self, request):
        return render(request, 'moda/cor_list.html', {
            'title': 'Cores',
            'cores': Cor.objects.for_filial(_filial(request)),
        })


class CorFormView(ModaBaseView):
    permissao_acao = 'criar'

    def get(self, request, pk=None):
        cor = get_object_or_404(Cor.objects.for_filial(_filial(request)), pk=pk) if pk else None
        return render(request, 'moda/cor_form.html', {
            'title': f'Cor {cor.nome}' if cor else 'Nova Cor',
            'cor': cor,
            'form': CorForm(instance=cor, filial=_filial(request)),
        })

    def post(self, request, pk=None):
        cor = get_object_or_404(Cor.objects.for_filial(_filial(request)), pk=pk) if pk else None
        form = CorForm(request.POST, instance=cor, filial=_filial(request))
        if not form.is_valid():
            return render(request, 'moda/cor_form.html', {
                'title': 'Nova Cor' if cor is None else f'Cor {cor.nome}',
                'cor': cor, 'form': form,
            })
        cor = form.save(commit=False)
        cor.filial = _filial(request)
        cor.save()
        messages.success(request, f'Cor {cor.nome} ({cor.sigla}) salva.')
        return redirect(reverse('moda:cor-list'))


# ══════════════════════════════════════════════════════════════════════
# PRODUTOS
# ══════════════════════════════════════════════════════════════════════

class ProdutoListView(ModaBaseView):
    def get(self, request):
        busca = (request.GET.get('q') or '').strip()
        produtos = (
            ProdutoModa.objects.for_filial(_filial(request))
            .select_related('categoria', 'colecao', 'tecido', 'grade', 'marca')
            .annotate(qtd_variantes=Count('variantes', distinct=True))
        )
        if busca:
            from django.db.models import Q
            produtos = produtos.filter(
                Q(nome__icontains=busca) | Q(codigo__icontains=busca)
                | Q(referencia__icontains=busca)
            )
        return render(request, 'moda/produto_list.html', {
            'title': 'Produtos de Moda',
            'produtos': produtos,
            'busca': busca,
        })


class ProdutoFormView(ModaBaseView):
    permissao_acao = 'criar'

    def get(self, request, pk=None):
        produto = get_object_or_404(ProdutoModa.objects.for_filial(_filial(request)), pk=pk) if pk else None
        return render(request, 'moda/produto_form.html', {
            'title': f'{produto.codigo}' if produto else 'Novo Produto',
            'produto': produto,
            'form': ProdutoModaForm(instance=produto, filial=_filial(request)),
        })

    def post(self, request, pk=None):
        produto = get_object_or_404(ProdutoModa.objects.for_filial(_filial(request)), pk=pk) if pk else None
        form = ProdutoModaForm(
            request.POST, request.FILES, instance=produto, filial=_filial(request),
        )
        if not form.is_valid():
            return render(request, 'moda/produto_form.html', {
                'title': 'Novo Produto' if produto is None else produto.codigo,
                'produto': produto, 'form': form,
            })
        produto = form.save(commit=False)
        produto.filial = _filial(request)
        produto.save()
        messages.success(request, f'Produto {produto.codigo} salvo.')
        return redirect(reverse('moda:produto-detail', args=[produto.pk]))


class ProdutoDetailView(ModaBaseView):
    def get(self, request, pk):
        produto = get_object_or_404(
            ProdutoModa.objects.for_filial(_filial(request))
            .select_related('categoria', 'colecao', 'linha', 'modelo', 'marca', 'tecido', 'grade'),
            pk=pk,
        )
        # Montada aqui, e não com 15 `{% if %}` no template: composição,
        # gramatura, gola e manga são properties derivadas de outros
        # cadastros, e a tela só precisa do par rótulo/valor.
        ficha = [
            ('Código', produto.codigo),
            ('Referência', produto.referencia),
            ('Status', produto.get_status_display()),
            ('Categoria', produto.categoria),
            ('Coleção', produto.colecao),
            ('Linha', produto.linha),
            ('Marca', produto.marca),
            ('Modelo', produto.modelo),
            ('Gola', produto.gola),
            ('Manga', produto.manga),
            ('Tecido', produto.tecido),
            ('Composição', produto.composicao),
            ('Gramatura', f'{produto.gramatura} g/m²' if produto.gramatura else ''),
            ('Grade', produto.grade),
        ]
        return render(request, 'moda/produto_detail.html', {
            'title': produto.codigo,
            'produto': produto,
            'ficha': ficha,
            'cores_produto': produto.cores.select_related('cor').all(),
            'variantes': produto.variantes.select_related('produto_cor__cor', 'tamanho').all(),
            'cores_disponiveis': Cor.objects.for_filial(_filial(request)).filter(ativo=True),
            'previa': VarianteService.previa(produto),
        })


class ProdutoCorAddView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk):
        produto = get_object_or_404(ProdutoModa.objects.for_filial(_filial(request)), pk=pk)
        cor_id = request.POST.get('cor')
        if cor_id:
            cor = get_object_or_404(Cor.objects.for_filial(_filial(request)), pk=cor_id)
            _, criou = ProdutoCor.objects.get_or_create(produto=produto, cor=cor)
            messages.success(
                request,
                f'Cor {cor.nome} adicionada.' if criou else f'{cor.nome} já estava no produto.',
            )
        return redirect(reverse('moda:produto-detail', args=[produto.pk]))


class ProdutoGerarVariantesView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk):
        produto = get_object_or_404(ProdutoModa.objects.for_filial(_filial(request)), pk=pk)
        try:
            resultado = VarianteService.gerar(produto)
        except DadosInvalidosError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, resultado.mensagem)
        return redirect(reverse('moda:produto-detail', args=[produto.pk]))
