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
from django.db.models import ProtectedError, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

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
            ('Estoque (m)', 'estoque_atual'),
        ),
        busca_em=('nome', 'composicao'),
        ajuda='Composição e gramatura ficam aqui, não no produto — o produto lê daqui.',
    ),
    'cadastro-aviamentos': Cadastro(
        slug='cadastro-aviamentos', model=m.Aviamento, form=f.AviamentoForm,
        singular='Aviamento', plural='Cadastro de Aviamentos', grupo='engenharia',
        colunas=(
            ('Nome', 'nome'), ('Tipo', 'get_tipo_display'),
            ('Código', 'codigo'), ('Fornecedor', 'fornecedor'),
        ),
        busca_em=('nome', 'codigo'),
        ordem=('tipo', 'nome'),
        ajuda=(
            'Cadastre uma vez — linha, elástico, zíper, botão... — e a ficha '
            'técnica escolhe daqui em vez de digitar tudo de novo em cada peça.'
        ),
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
                # `attr` viaja junto do valor pra tela reconhecer a coluna
                # de estoque e oferecer a edição rápida só nela -- as
                # outras colunas (nome, composição...) não têm produto de
                # estoque nenhum atrás pra editar.
                'valores': [
                    {'attr': attr, 'valor': _valor(obj, attr)}
                    for _rot, attr in cadastro.colunas
                ],
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

        produto_criado_id = request.GET.get('produto_criado')
        if obj is not None and produto_criado_id and hasattr(obj, 'produto_estoque_id'):
            self._vincular_produto_criado(request, obj, produto_criado_id)
            # Redireciona pra a mesma tela sem o `produto_criado` na URL --
            # senão um F5 tentaria vincular de novo o mesmo produto.
            return redirect(reverse('moda:apoio-update', args=[cadastro.grupo, cadastro.slug, obj.pk]))

        return render(request, 'moda/apoio_form.html', {
            'title': str(obj) if obj else f'Novo(a) {cadastro.singular}',
            'cadastro': cadastro,
            'obj': obj,
            'form': cadastro.form(instance=obj, filial=request.filial_ativa),
        })

    @staticmethod
    def _vincular_produto_criado(request, obj, produto_id):
        """
        Volta do "+ Novo produto": o produto acabou de nascer no módulo de
        Produtos (com a quantidade inicial já lançada por lá) e falta só
        ligar ao cadastro -- sem isso o usuário teria que reabrir o
        formulário e escolher da lista de novo.
        """
        from apps.produtos.models import Produto
        try:
            produto = Produto.objects.get(pk=produto_id, filial=request.filial_ativa)
        except (Produto.DoesNotExist, ValueError, TypeError):
            messages.error(request, 'Não foi possível vincular o produto criado.')
            return
        obj.produto_estoque = produto
        obj.save(update_fields=['produto_estoque'])
        messages.success(request, f'Produto "{produto}" criado e vinculado.')

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


def _redirecionar(request, cadastro):
    """
    Volta pra onde o botão foi clicado, quando é seguro -- inativar/excluir
    tecido a partir da tela de Estoque › Tecidos deveria devolver pra lá, e
    não sempre pro cadastro genérico, senão quem usou o atalho perde o
    lugar de onde saiu.
    """
    destino = request.POST.get('next', '')
    if destino and url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
        return redirect(destino)
    return redirect(reverse('moda:item', args=[cadastro.grupo, cadastro.slug]))


class CadastroApoioToggleAtivoView(ModaBaseView):
    """Ativa/inativa sem apagar -- é a saída pra tirar de circulação um
    cadastro que já foi usado (tecido descontinuado, tamanho que saiu de
    linha) sem quebrar o histórico que aponta pra ele."""

    permissao_acao = 'editar'

    def post(self, request, slug, pk, grupo=None):
        cadastro = _cadastro(slug)
        obj = get_object_or_404(
            cadastro.model.objects.for_filial(request.filial_ativa), pk=pk,
        )
        obj.ativo = not obj.ativo
        obj.save(update_fields=['ativo'])
        acao = 'ativado(a)' if obj.ativo else 'inativado(a)'
        messages.success(request, f'{cadastro.singular} "{obj}" {acao}.')
        return _redirecionar(request, cadastro)


class CadastroApoioDeleteView(ModaBaseView):
    """
    Exclui de verdade -- só quando dá. Todo FK que aponta pra um cadastro
    de apoio (tecido no produto, no corte, no encaixe, no item de pedido...)
    é PROTECT, então o próprio banco recusa apagar o que já está em uso; o
    caminho pra esses é inativar, não excluir.
    """

    permissao_acao = 'excluir'

    def post(self, request, slug, pk, grupo=None):
        cadastro = _cadastro(slug)
        obj = get_object_or_404(
            cadastro.model.objects.for_filial(request.filial_ativa), pk=pk,
        )
        nome = str(obj)
        try:
            obj.delete()
        except ProtectedError:
            messages.error(
                request,
                f'{cadastro.singular} "{nome}" está em uso e não pode ser excluído(a). '
                'Inative em vez de excluir.',
            )
        else:
            messages.success(request, f'{cadastro.singular} "{nome}" excluído(a).')
        return _redirecionar(request, cadastro)
