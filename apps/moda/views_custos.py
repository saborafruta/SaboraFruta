"""
Custos — onde o dinheiro da peça está indo.

O custo já era calculado, mas espalhado: a ficha soma os materiais, o
roteiro soma a mão de obra, e a estrutura soma as partes. Cada um respondia
por si, e ninguém respondia a pergunta que se faz de verdade — quanto custa
cada produto, do que esse custo é feito, e quais produtos ainda não dá para
custear.

NADA É GRAVADO. O total sai da leitura, como na ficha e na estrutura: um
custo gravado fica velho no instante em que alguém corrige o preço de um
aviamento, e ninguém confia num número que pode estar velho.

O ACHADO DESTA TELA é o material sem preço. Um `custo_unitario` em branco
não quebra nada: ele entra como zero e o custo da peça sai MENOR do que é,
sem aviso nenhum. Quem olha um custo subestimado fecha preço com prejuízo e
só descobre no fim do mês. Por isso a contagem tem cartão próprio e filtro.
"""
from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import render

from .models import MaterialFicha, ProdutoModa
from .services import estrutura as servico_estrutura
from .views import ModaBaseView

ZERO = Decimal('0')


def _custo_materiais(produto) -> Decimal:
    ficha = getattr(produto, 'ficha', None)
    return ficha.custo_estimado if ficha else ZERO


def _custo_mao_de_obra(produto) -> Decimal:
    # OneToOne sem objeto levanta uma excecao que herda de AttributeError,
    # entao o getattr com default resolve sem try/except.
    roteiro = getattr(produto, 'roteiro', None)
    return roteiro.custo_total if roteiro else ZERO


class CustoListView(ModaBaseView):
    """O custo de cada produto, e do que ele é feito."""

    area = 'engenharia'

    def get(self, request):
        filial = request.filial_ativa
        busca = (request.GET.get('q') or '').strip()
        filtro = (request.GET.get('filtro') or '').strip()
        ordem = (request.GET.get('ordem') or 'custo').strip()

        produtos = (
            ProdutoModa.objects.for_filial(filial)
            .select_related('ficha', 'roteiro')
            .prefetch_related('ficha__materiais', 'roteiro__etapas')
            .annotate(
                # Material "sem preço" é `custo_unitario = 0`, e não nulo: o
                # campo tem `default=0` e não aceita nulo, então em branco no
                # formulário grava zero. Some no total sem avisar.
                sem_preco=Count(
                    'ficha__materiais',
                    filter=Q(ficha__materiais__custo_unitario=0),
                    distinct=True,
                ),
                qtd_componentes=Count('componentes', distinct=True),
            )
        )
        if busca:
            produtos = produtos.filter(
                Q(nome__icontains=busca) | Q(codigo__icontains=busca)
                | Q(referencia__icontains=busca)
            )

        linhas = []
        for produto in produtos:
            materiais = _custo_materiais(produto)
            mao_de_obra = _custo_mao_de_obra(produto)
            # O custo das partes: a estrutura inteira menos o que é da
            # própria peça, senão os materiais dela entrariam duas vezes.
            componentes = (
                servico_estrutura.custo_estrutura(produto) - materiais
                if produto.qtd_componentes else ZERO
            )
            total = (materiais + mao_de_obra + componentes).quantize(Decimal('0.01'))
            linhas.append({
                'produto': produto,
                'materiais': materiais,
                'mao_de_obra': mao_de_obra,
                'componentes': componentes,
                'total': total,
                'sem_ficha': getattr(produto, 'ficha', None) is None,
                'sem_roteiro': getattr(produto, 'roteiro', None) is None,
                'sem_preco': produto.sem_preco,
                'por_tipo': produto.ficha.custo_por_tipo if getattr(produto, 'ficha', None) else [],
            })

        resumo = {
            'total': len(linhas),
            'sem_ficha': sum(1 for l in linhas if l['sem_ficha']),
            'sem_preco': sum(1 for l in linhas if l['sem_preco']),
            'sem_roteiro': sum(1 for l in linhas if l['sem_roteiro'] and not l['sem_ficha']),
        }

        if filtro == 'sem_ficha':
            linhas = [l for l in linhas if l['sem_ficha']]
        elif filtro == 'sem_preco':
            linhas = [l for l in linhas if l['sem_preco']]
        elif filtro == 'sem_roteiro':
            linhas = [l for l in linhas if l['sem_roteiro'] and not l['sem_ficha']]
        elif filtro == 'custeados':
            linhas = [l for l in linhas if not l['sem_ficha']]

        # Do maior custo para o menor por padrão: a pergunta que se faz
        # olhando esta tela é "onde está indo o dinheiro", e a resposta tem
        # de estar na primeira linha.
        if ordem == 'nome':
            linhas.sort(key=lambda l: l['produto'].nome.upper())
        else:
            linhas.sort(key=lambda l: l['total'], reverse=True)

        parametros = request.GET.copy()
        parametros.pop('page', None)

        return render(request, 'moda/custo_list.html', {
            'title': 'Custos',
            'page_obj': Paginator(linhas, 40).get_page(request.GET.get('page')),
            'page_querystring': parametros.urlencode(),
            'resumo': resumo,
            'busca': busca,
            'filtro': filtro,
            'ordem': ordem,
            'tem_filtro': bool(busca or filtro),
        })
