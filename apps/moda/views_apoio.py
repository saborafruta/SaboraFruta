"""
Telas dos cadastros de apoio (Tamanhos, Modelos, Tecidos, Marcas, Coleções…).

Todos têm a mesma forma: lista com busca e formulário de criar/editar,
escopados por filial. Escrever dez views e dez templates quase idênticos
significaria dez lugares para corrigir o mesmo detalhe.

Aqui há um par de views genéricas guiado por `CADASTROS`. Acrescentar um
sexto cadastro é uma entrada nesse dicionário — sem view, sem template e
sem rota nova escritos à mão.
"""
from dataclasses import dataclass, field

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from . import forms as f
from . import models as m
from .views import ModaBaseView


@dataclass(frozen=True)
class Cadastro:
    slug: str
    model: type
    form: type
    singular: str
    plural: str
    # Grupo do menu a que pertence, para a trilha de navegação.
    grupo: str
    # Campos mostrados na lista: (rótulo, atributo). O primeiro é o link.
    colunas: tuple[tuple[str, str], ...]
    # Campos varridos pela busca.
    busca_em: tuple[str, ...] = ('nome',)
    ajuda: str = ''
    ordem: tuple[str, ...] = field(default_factory=lambda: ('nome',))


CADASTROS: dict[str, Cadastro] = {
    'tamanhos': Cadastro(
        slug='tamanhos', model=m.Tamanho, form=f.TamanhoForm,
        singular='Tamanho', plural='Tamanhos', grupo='produtos',
        colunas=(('Sigla', 'sigla'), ('Nome', 'nome'), ('Tipo', 'get_tipo_display'), ('Ordem', 'ordem')),
        busca_em=('sigla', 'nome'),
        ordem=('tipo', 'ordem', 'sigla'),
        ajuda='A ordem define a sequência na grade — é ela que faz a ficha sair PP, P, M, G.',
    ),
    'modelos': Cadastro(
        slug='modelos', model=m.Modelo, form=f.ModeloForm,
        singular='Modelo', plural='Modelos', grupo='produtos',
        colunas=(('Nome', 'nome'), ('Gola', 'get_gola_display'), ('Manga', 'get_manga_display')),
        ajuda='Gola e manga daqui viram o padrão do item no pedido, que pode sobrescrever.',
    ),
    'colecoes': Cadastro(
        slug='colecoes', model=m.Colecao, form=f.ColecaoForm,
        singular='Coleção', plural='Coleções', grupo='produtos',
        colunas=(('Nome', 'nome'), ('Ano', 'ano'), ('Estação', 'estacao')),
        busca_em=('nome', 'estacao'),
        ordem=('-ano', 'nome'),
    ),
    'categorias': Cadastro(
        slug='categorias', model=m.Categoria, form=f.CategoriaForm,
        singular='Categoria', plural='Categorias', grupo='produtos',
        colunas=(('Nome', 'nome'), ('Dentro de', 'pai')),
        ajuda='Deixe "Dentro de" vazio para categoria raiz; preencha para criar uma subcategoria.',
    ),
    'marcas': Cadastro(
        slug='marcas', model=m.Marca, form=f.MarcaForm,
        singular='Marca', plural='Marcas', grupo='produtos',
        colunas=(('Nome', 'nome'), ('Observação', 'observacao')),
    ),
    'linhas': Cadastro(
        slug='linhas', model=m.Linha, form=f.LinhaForm,
        singular='Linha', plural='Linhas', grupo='produtos',
        colunas=(('Nome', 'nome'), ('Observação', 'observacao')),
        ajuda='Linha de produto: Esportiva, Casual, Uniforme profissional.',
    ),
    'operacoes': Cadastro(
        slug='operacoes', model=m.Operacao, form=f.OperacaoForm,
        singular='Operação', plural='Operações', grupo='engenharia',
        colunas=(
            ('Operação', 'nome'), ('Setor', 'get_setor_display'),
            ('Máquina', 'maquina'), ('Tempo (min)', 'tempo_padrao'),
            ('Custo/peça', 'custo_por_peca'), ('Cap. (pç/h)', 'capacidade'),
        ),
        busca_em=('nome', 'maquina', 'responsavel'),
        ordem=('sequencia', 'nome'),
        ajuda=(
            'O catálogo da fábrica. O roteiro de cada produto escolhe daqui '
            'quais operações usa e em que ordem. As 15 padrão saem de uma vez '
            'pelo comando seed_operacoes_moda.'
        ),
    ),
    'materiais': Cadastro(
        slug='materiais', model=m.Tecido, form=f.TecidoForm,
        singular='Tecido', plural='Tecidos e Malhas', grupo='engenharia',
        colunas=(
            ('Nome', 'nome'), ('Composição', 'composicao'),
            ('Gramatura', 'gramatura'), ('Fornecedor', 'fornecedor'),
        ),
        busca_em=('nome', 'composicao'),
        ajuda='Composição e gramatura ficam aqui, não no produto — o produto lê daqui.',
    ),
}


def _cadastro(slug: str) -> Cadastro:
    cadastro = CADASTROS.get(slug)
    if cadastro is None:
        from django.http import Http404
        raise Http404('Cadastro não existe.')
    return cadastro


def _valor(obj, atributo):
    """Lê o atributo, chamando quando for método (`get_tipo_display`)."""
    valor = getattr(obj, atributo, None)
    return valor() if callable(valor) else valor


class CadastroApoioListView(ModaBaseView):
    def get(self, request, slug):
        cadastro = _cadastro(slug)
        busca = (request.GET.get('q') or '').strip()

        qs = cadastro.model.objects.for_filial(request.filial_ativa)
        if busca:
            filtro = Q()
            for campo in cadastro.busca_em:
                filtro |= Q(**{f'{campo}__icontains': busca})
            qs = qs.filter(filtro)
        qs = qs.order_by(*cadastro.ordem)

        linhas = [
            {
                'obj': obj,
                'valores': [_valor(obj, attr) for _rot, attr in cadastro.colunas],
            }
            for obj in qs
        ]
        return render(request, 'moda/apoio_list.html', {
            'title': cadastro.plural,
            'cadastro': cadastro,
            'linhas': linhas,
            'busca': busca,
        })


class CadastroApoioFormView(ModaBaseView):
    permissao_acao = 'criar'

    def _obter(self, request, cadastro, pk):
        if pk is None:
            return None
        return get_object_or_404(
            cadastro.model.objects.for_filial(request.filial_ativa), pk=pk,
        )

    # `grupo` vem da URL só para o endereço espelhar o menu; quem manda
    # é o slug, que identifica o cadastro.
    def get(self, request, slug, pk=None, grupo=None):
        cadastro = _cadastro(slug)
        obj = self._obter(request, cadastro, pk)
        return render(request, 'moda/apoio_form.html', {
            'title': str(obj) if obj else f'Novo(a) {cadastro.singular}',
            'cadastro': cadastro,
            'obj': obj,
            'form': cadastro.form(instance=obj, filial=request.filial_ativa),
        })

    def post(self, request, slug, pk=None, grupo=None):
        cadastro = _cadastro(slug)
        obj = self._obter(request, cadastro, pk)
        form = cadastro.form(request.POST, instance=obj, filial=request.filial_ativa)

        if not form.is_valid():
            return render(request, 'moda/apoio_form.html', {
                'title': str(obj) if obj else f'Novo(a) {cadastro.singular}',
                'cadastro': cadastro, 'obj': obj, 'form': form,
            })

        obj = form.save(commit=False)
        obj.filial = request.filial_ativa
        obj.save()
        messages.success(request, f'{cadastro.singular} "{obj}" salvo(a).')
        return redirect(reverse('moda:item', args=[cadastro.grupo, cadastro.slug]))
