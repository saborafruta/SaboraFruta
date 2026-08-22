"""
Estrutura do produto — a composição em níveis.

O "Conjunto — Camisa + Calção" que já aparecia como TEXTO na descrição do
item do pedido passa a existir como dado: produzir o conjunto é produzir a
camisa e o calção, e o custo dele sai da soma das partes em vez de ser
digitado à mão.

CADA TELA EDITA O NÍVEL QUE É DELA. A tela de um produto mexe só nos
componentes DIRETOS dele; o que está mais fundo aparece para leitura, com
link para a tela do próprio dono. Deixar editar o neto aqui daria dois
lugares para mudar a mesma linha, e a segunda pessoa a salvar apagaria o
trabalho da primeira sem aviso.
"""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .models import EstruturaProduto, ProdutoModa
from .services import estrutura as servico
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


def _produto(request, pk):
    return get_object_or_404(
        ProdutoModa.objects.for_filial(_filial(request)).select_related('ficha'), pk=pk,
    )


class EstruturaListView(ModaBaseView):
    """Quais produtos são compostos, e quais ainda são peça solta."""

    area = 'engenharia'

    def get(self, request):
        filial = _filial(request)
        busca = (request.GET.get('q') or '').strip()
        filtro = (request.GET.get('filtro') or '').strip()

        produtos = (
            ProdutoModa.objects.for_filial(filial)
            .annotate(
                qtd_componentes=Count('componentes', distinct=True),
                qtd_usos=Count('usado_em', distinct=True),
            )
            .order_by('nome')
        )
        if busca:
            produtos = produtos.filter(
                Q(nome__icontains=busca) | Q(codigo__icontains=busca)
                | Q(referencia__icontains=busca)
            )
        if filtro == 'compostos':
            produtos = produtos.filter(qtd_componentes__gt=0)
        elif filtro == 'soltos':
            produtos = produtos.filter(qtd_componentes=0)

        return render(request, 'moda/estrutura_list.html', {
            'title': 'Estrutura do Produto',
            'page_obj': Paginator(produtos, 40).get_page(request.GET.get('page')),
            'page_querystring': self._querystring(request),
            'busca': busca,
            'filtro': filtro,
            'tem_filtro': bool(busca or filtro),
            'total_compostos': (
                ProdutoModa.objects.for_filial(filial)
                .annotate(n=Count('componentes')).filter(n__gt=0).count()
            ),
        })

    @staticmethod
    def _querystring(request) -> str:
        parametros = request.GET.copy()
        parametros.pop('page', None)
        return parametros.urlencode()


class EstruturaDetailView(ModaBaseView):
    """A árvore de um produto, com os componentes diretos editáveis."""

    area = 'engenharia'

    def get(self, request, pk):
        produto = _produto(request, pk)
        return render(request, 'moda/estrutura_detail.html', self.contexto(request, produto))

    @staticmethod
    def contexto(request, produto, **extra):
        diretos = (
            EstruturaProduto.objects.filter(pai=produto)
            .select_related('componente', 'componente__ficha')
            .order_by('ordem', 'id')
        )
        # Candidatos: tudo da filial menos o próprio produto e o que já está
        # na estrutura. O ciclo NÃO é filtrado aqui -- descobrir isso exige
        # descer a árvore de cada candidato, e o preço disso numa lista de
        # centenas não paga. Quem barra é `pode_incluir`, na gravação, com
        # uma frase que diz qual peça fecha o ciclo.
        candidatos = (
            ProdutoModa.objects.for_filial(request.filial_ativa)
            .exclude(pk=produto.pk)
            .exclude(pk__in=diretos.values_list('componente_id', flat=True))
            .order_by('nome')
        )
        contexto = {
            'title': f'Estrutura — {produto.nome}',
            'produto': produto,
            'diretos': diretos,
            'arvore': servico.arvore(produto),
            'custo_proprio': servico.custo_proprio(produto),
            'custo_total': servico.custo_estrutura(produto),
            'candidatos': candidatos,
            'usado_em': (
                EstruturaProduto.objects.filter(componente=produto)
                .select_related('pai').order_by('pai__nome')
            ),
        }
        contexto.update(extra)
        return contexto


class EstruturaAddView(ModaBaseView):
    """Acrescenta um componente direto."""

    area = 'engenharia'
    permissao_acao = 'editar'

    def post(self, request, pk):
        produto = _produto(request, pk)
        componente_id = (request.POST.get('componente') or '').strip()

        if not componente_id.isdigit():
            messages.error(request, 'Escolha o componente.')
            return redirect(reverse('moda:estrutura-detail', args=[produto.pk]))

        componente = get_object_or_404(
            ProdutoModa.objects.for_filial(_filial(request)), pk=componente_id,
        )
        pode, motivo = servico.pode_incluir(produto, componente)
        if not pode:
            messages.error(request, motivo)
            return redirect(reverse('moda:estrutura-detail', args=[produto.pk]))

        quantidade = self._quantidade(request.POST.get('quantidade'))
        if quantidade is None:
            messages.error(request, 'A quantidade precisa ser um número maior que zero.')
            return redirect(reverse('moda:estrutura-detail', args=[produto.pk]))

        ultima = (
            EstruturaProduto.objects.filter(pai=produto)
            .order_by('-ordem').values_list('ordem', flat=True).first() or 0
        )
        EstruturaProduto.objects.create(
            pai=produto, componente=componente,
            quantidade=quantidade, ordem=ultima + 10,
            observacao=(request.POST.get('observacao') or '').strip()[:160],
        )
        messages.success(
            request, f'{quantidade:g} × {componente.nome} acrescentado à estrutura.',
        )
        return redirect(reverse('moda:estrutura-detail', args=[produto.pk]))

    @staticmethod
    def _quantidade(bruto):
        from decimal import Decimal, InvalidOperation
        try:
            valor = Decimal((bruto or '1').replace(',', '.'))
        except (InvalidOperation, AttributeError):
            return None
        return valor if valor > 0 else None


class EstruturaSalvarView(ModaBaseView):
    """Grava as quantidades da estrutura de uma vez, como a grade do pedido."""

    area = 'engenharia'
    permissao_acao = 'editar'

    def post(self, request, pk):
        produto = _produto(request, pk)
        alterados = 0

        for elo in EstruturaProduto.objects.filter(pai=produto):
            bruto = request.POST.get(f'qtd_{elo.pk}')
            if bruto is None:
                continue
            quantidade = EstruturaAddView._quantidade(bruto)
            if quantidade is None:
                messages.error(
                    request,
                    f'Quantidade inválida em {elo.componente.nome} — nada foi gravado.',
                )
                return redirect(reverse('moda:estrutura-detail', args=[produto.pk]))
            if elo.quantidade != quantidade:
                elo.quantidade = quantidade
                elo.save(update_fields=['quantidade'])
                alterados += 1

        messages.success(
            request,
            f'{alterados} quantidade(s) atualizada(s).' if alterados
            else 'Nada mudou na estrutura.',
        )
        return redirect(reverse('moda:estrutura-detail', args=[produto.pk]))


class EstruturaRemoveView(ModaBaseView):
    """Tira um componente da estrutura. Não apaga o produto do catálogo."""

    area = 'engenharia'
    permissao_acao = 'editar'

    def post(self, request, pk, elo_pk):
        produto = _produto(request, pk)
        elo = get_object_or_404(
            EstruturaProduto.objects.select_related('componente'), pk=elo_pk, pai=produto,
        )
        nome = elo.componente.nome
        elo.delete()
        messages.success(request, f'{nome} saiu da estrutura de {produto.nome}.')
        return redirect(reverse('moda:estrutura-detail', args=[produto.pk]))
