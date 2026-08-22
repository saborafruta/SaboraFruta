"""
Aviamentos — o catálogo do que já é usado, consolidado.

Hoje um aviamento só existe DENTRO de uma ficha técnica: é uma linha de
`MaterialFicha` com descrição, consumo e custo. Isso responde "o que entra
nesta peça", e não responde nada do outro lado: quais produtos usam o zíper
nº 5, quanto se gasta dele no total, se o preço está o mesmo em todas as
fichas, e quais aviamentos ainda não foram ligados ao estoque.

Essa última é a que dói: sem `produto_estoque` o sistema não consegue ler
saldo nem reservar — a necessidade de material continua sendo calculada,
mas ninguém sabe se falta.

A tela é de LEITURA. O aviamento continua sendo cadastrado onde ele existe,
que é dentro da ficha da peça; duplicar aqui um cadastro próprio criaria
duas verdades para o mesmo zíper.
"""
from decimal import Decimal

from django.db.models import Q
from django.shortcuts import render

from .models import MaterialFicha
from .views import ModaBaseView

# O que é aviamento, e o que é tecido. A divisão segue a ficha de papel:
# tecido e forro são o corpo da peça; o resto é o que se prega nela.
TIPOS_AVIAMENTO = [
    MaterialFicha.Tipo.LINHA,
    MaterialFicha.Tipo.ELASTICO,
    MaterialFicha.Tipo.ZIPER,
    MaterialFicha.Tipo.BOTAO,
    MaterialFicha.Tipo.ETIQUETA,
    MaterialFicha.Tipo.TAG,
    MaterialFicha.Tipo.EMBALAGEM,
    MaterialFicha.Tipo.AVIAMENTO,
]


def _chave(material) -> tuple:
    """
    O que faz duas linhas de ficha serem o MESMO aviamento.

    O código manda quando existe, porque é o que o estoque e o fornecedor
    reconhecem. Sem código sobra a descrição, e aí "Zíper nº 5" e "Ziper n5"
    ficam separados — o que é chato, mas é honesto: juntá-los por
    semelhança esconderia justamente o cadastro que precisa ser padronizado.
    """
    codigo = (material.codigo or '').strip().upper()
    return (material.tipo, codigo or (material.descricao or '').strip().upper())


class AviamentoListView(ModaBaseView):
    """Linhas, botões, zíperes e etiquetas — de todas as fichas da filial."""

    area = 'engenharia'

    def get(self, request):
        busca = (request.GET.get('q') or '').strip()
        tipo = (request.GET.get('tipo') or '').strip()
        filtro = (request.GET.get('filtro') or '').strip()

        materiais = (
            MaterialFicha.objects
            .filter(ficha__filial=request.filial_ativa, tipo__in=TIPOS_AVIAMENTO)
            .select_related('ficha', 'ficha__produto', 'produto_estoque')
            .order_by('tipo', 'descricao')
        )
        if busca:
            materiais = materiais.filter(
                Q(descricao__icontains=busca) | Q(codigo__icontains=busca)
                | Q(ficha__produto__nome__icontains=busca)
            )
        if tipo in [t.value for t in TIPOS_AVIAMENTO]:
            materiais = materiais.filter(tipo=tipo)

        lista = agrupar(materiais)

        resumo = {
            'total': len(lista),
            'sem_estoque': sum(1 for g in lista if g['sem_estoque']),
            'preco_divergente': sum(1 for g in lista if g['preco_divergente']),
        }

        if filtro == 'sem_estoque':
            lista = [g for g in lista if g['sem_estoque']]
        elif filtro == 'preco_divergente':
            lista = [g for g in lista if g['preco_divergente']]

        return render(request, 'moda/aviamento_list.html', {
            'title': 'Aviamentos',
            'aviamentos': lista,
            'resumo': resumo,
            'busca': busca,
            'tipo': tipo,
            'filtro': filtro,
            'tipos': [(t.value, t.label) for t in TIPOS_AVIAMENTO],
            'tem_filtro': bool(busca or tipo or filtro),
        })


def agrupar(materiais) -> list[dict]:
    """
    Junta as linhas de ficha que são o mesmo aviamento.

    Fora da view de propósito: é a única regra desta tela que pode errar em
    silêncio — juntar demais esconde um cadastro duplicado, juntar de menos
    inventa aviamento que não existe — e regra assim precisa de teste, que
    não se escreve contra uma view que exige login e filial na sessão.
    """
    grupos = {}
    for material in materiais:
        grupo = grupos.setdefault(_chave(material), {
            'tipo': material.tipo,
            'tipo_rotulo': material.get_tipo_display(),
            'descricao': material.descricao,
            'codigo': material.codigo,
            'unidades': set(),
            'usos': [],
            'consumo_total': Decimal('0'),
            'custo_total': Decimal('0'),
            'precos': set(),
            'no_estoque': None,
        })
        grupo['unidades'].add(material.get_unidade_display())
        grupo['usos'].append(material)
        grupo['consumo_total'] += material.consumo_bruto
        grupo['custo_total'] += material.custo_total
        if material.custo_unitario:
            grupo['precos'].add(material.custo_unitario)
        if material.produto_estoque_id and grupo['no_estoque'] is None:
            grupo['no_estoque'] = material.produto_estoque

    for grupo in grupos.values():
        grupo['qtd_usos'] = len(grupo['usos'])
        grupo['unidades'] = sorted(grupo['unidades'])
        # PREÇO DIVERGENTE é achado, não detalhe: o mesmo zíper com dois
        # valores em fichas diferentes significa que um dos dois custos está
        # velho, e o custo da peça sai errado dos dois jeitos.
        grupo['precos'] = sorted(grupo['precos'])
        grupo['preco_divergente'] = len(grupo['precos']) > 1
        grupo['sem_estoque'] = grupo['no_estoque'] is None

    return sorted(
        grupos.values(),
        key=lambda g: (g['tipo_rotulo'], (g['descricao'] or '').upper()),
    )
