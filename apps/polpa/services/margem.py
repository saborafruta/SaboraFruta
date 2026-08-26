"""
Margem de contribuição: o que sobra de cada batida depois do custo variável.

CUSTO SEM PREÇO NÃO DECIDE NADA. `CustoService` diz quanto a batida consumiu;
esta diz se o que foi cobrado cobriu aquilo. É por ela que se descobre o
produto que a fábrica faz bem e vende barato demais.

O CUSTO VEM PRONTO DE `CustoService`, e não é recalculado aqui. Duas
implementações do mesmo custo divergem, e aí Custos e Margem dariam números
diferentes para a mesma ordem — o tipo de discordância que faz as duas telas
perderem a confiança de uma vez.

CONTRIBUIÇÃO NÃO É LUCRO, e a diferença é o ponto desta tela. Margem de
contribuição desconta só o custo VARIÁVEL — o que existe porque aquela batida
existiu. Mão de obra e rateio de indireto continuam correndo com a linha
parada; incluí-los aqui daria margem líquida com nome de contribuição, e a
decisão que a contribuição sustenta ("vale a pena produzir mais este item?")
passaria a ser respondida com o número errado.

O QUE É VARIÁVEL, e por quê:

  · MATÉRIA-PRIMA e EMBALAGEM — não se compra fruta para batida que não vai
    acontecer;
  · PERDA REGISTRADA — é material que saiu e não virou produto, e ele foi
    comprado por causa desta batida;
  · CUSTOS EXTRAS POR QUILO OU POR UNIDADE — energia do túnel dobra quando se
    congela o dobro. Os POR BATIDA não: setup e higienização acontecem uma vez
    e não mudam com o tamanho, então são fixos.

Essa separação já estava no cadastro: `CustoReceita.Base` distingue batida de
quilo e unidade desde que os custos configuráveis existem. Aqui ela ganha
consequência.

BATIDA SEM PREÇO NÃO É PREJUÍZO. Produção para amostra, para teste de receita
ou para reposição entra com receita zero, e tratá-la como margem de −100%
afundaria a média de todo mundo. Fica de fora das médias e é contada à parte.
"""
from __future__ import annotations

from decimal import Decimal

from apps.polpa.models import CustoReceita, OrdemPolpa

ZERO = Decimal('0')
REAL = Decimal('0.01')

# As categorias de `CustoService` que existem por causa desta batida.
VARIAVEIS = ('materia_prima', 'embalagem', 'perdas')


class MargemService:

    @classmethod
    def da_ordem(cls, op: OrdemPolpa) -> dict:
        """
        A contribuição desta batida. `None` no que a conta não permite.

        Zero seria lido como "não sobrou nada", que é uma afirmação — e uma
        ordem sem preço cadastrado não afirma nada sobre margem.
        """
        from apps.polpa.services.custo import CustoService

        realizado = CustoService.realizado(op)
        produzida = op.ordem.quantidade_produzida or ZERO
        concluida = realizado['total'] is not None

        preco = cls._preco(op)
        receita = (preco * produzida) if preco is not None else None

        variavel = cls._variavel(op, realizado, produzida)
        fixo = cls._fixo(op, realizado, produzida)

        contribuicao = (
            (receita - variavel) if receita is not None and concluida else None
        )
        return {
            'concluida': concluida,
            'preco_unitario': preco,
            'quantidade': produzida,
            'receita': receita.quantize(REAL) if receita is not None else None,
            'custo_variavel': variavel.quantize(REAL),
            'custo_fixo': fixo.quantize(REAL),
            'custo_total': realizado['total'],
            'contribuicao': (
                contribuicao.quantize(REAL) if contribuicao is not None else None
            ),
            'contribuicao_unitaria': (
                (contribuicao / produzida).quantize(Decimal('0.0001'))
                if contribuicao is not None and produzida > ZERO else None
            ),
            'percentual': (
                (contribuicao / receita * 100).quantize(REAL)
                if contribuicao is not None and receita and receita > ZERO
                else None
            ),
            # O RESULTADO DEPOIS DO FIXO, ao lado. Contribuição positiva com
            # resultado negativo é a situação que a fábrica precisa enxergar:
            # o item paga o próprio material e não paga a estrutura.
            'resultado': (
                (receita - realizado['total']).quantize(REAL)
                if receita is not None and concluida else None
            ),
            'sem_preco': preco is None,
        }

    # ── As duas metades do custo ─────────────────────────────────────────

    @classmethod
    def _variavel(cls, op, realizado: dict, produzida) -> Decimal:
        base = sum((realizado.get(c) or ZERO for c in VARIAVEIS), ZERO)
        return base + cls._extras_por_base(op, produzida, fixos=False)

    @classmethod
    def _fixo(cls, op, realizado: dict, produzida) -> Decimal:
        base = (realizado.get('mao_de_obra') or ZERO) + (
            realizado.get('indireto') or ZERO
        )
        return base + cls._extras_por_base(op, produzida, fixos=True)

    @staticmethod
    def _extras_por_base(op, produzida, fixos: bool) -> Decimal:
        """
        Separa os custos configuráveis pela base que cada um declara.

        POR BATIDA é fixo: setup e higienização acontecem uma vez e não mudam
        com o tamanho. POR QUILO e POR UNIDADE acompanham o volume, então
        existem porque a batida existiu.
        """
        from apps.polpa.services.custo import CustoService

        peso = CustoService._peso_produzido(op, produzida)
        total = ZERO
        for custo in CustoReceita.all_objects.filter(
            receita=op.receita, ativo=True,
        ):
            por_batida = custo.base == CustoReceita.Base.BATIDA
            if por_batida is fixos:
                total += custo.total_para(produzida, peso)
        return total

    # ── A receita ────────────────────────────────────────────────────────

    @staticmethod
    def _preco(op: OrdemPolpa):
        """
        O preço de venda do produto. `None` quando não há.

        Produto sem preço não dá margem negativa: dá margem desconhecida. São
        coisas diferentes, e confundi-las faz a amostra e o teste de receita
        afundarem a média da fábrica.
        """
        preco = getattr(op.receita.produto, 'preco_venda', None)
        return preco if preco and preco > ZERO else None

    # ── Leitura de conjunto ──────────────────────────────────────────────

    @classmethod
    def painel(cls, filial, dias: int = 30) -> dict:
        """
        A contribuição das batidas encerradas no período.

        AS SEM PREÇO FICAM DE FORA DA MÉDIA e aparecem contadas: escondê-las
        faria a tela parecer completa quando metade da produção não entrou na
        conta, e é assim que alguém decide preço olhando meia fábrica.
        """
        from datetime import timedelta

        from django.utils import timezone

        desde = timezone.now() - timedelta(days=dias)
        ordens = (
            OrdemPolpa.objects.for_filial(filial)
            .filter(
                situacao=OrdemPolpa.Situacao.PRODUZIDA,
                ordem__data_fim_real__gte=desde,
            )
            # A ficha da receita é a do ERP, e lá o acabado se chama
            # `produto_acabado` — puxar por `ordem` evita repetir esse
            # detalhe de nome em toda consulta.
            .select_related('ordem', 'receita', 'ordem__produto_acabado')
        )

        linhas, sem_preco = [], []
        receita_total = variavel_total = fixo_total = ZERO
        for op in ordens:
            dados = cls.da_ordem(op)
            dados['ordem'] = op
            if dados['sem_preco'] or not dados['concluida']:
                sem_preco.append(dados)
                continue
            linhas.append(dados)
            receita_total += dados['receita'] or ZERO
            variavel_total += dados['custo_variavel']
            fixo_total += dados['custo_fixo']

        # Pior contribuição primeiro: é a que precisa de decisão.
        linhas.sort(key=lambda l: l['percentual'] if l['percentual'] is not None else ZERO)

        contribuicao = receita_total - variavel_total
        return {
            'linhas': linhas,
            'sem_preco': sem_preco,
            'receita': receita_total.quantize(REAL),
            'custo_variavel': variavel_total.quantize(REAL),
            'custo_fixo': fixo_total.quantize(REAL),
            'contribuicao': contribuicao.quantize(REAL),
            'percentual': (
                (contribuicao / receita_total * 100).quantize(REAL)
                if receita_total > ZERO else None
            ),
            # O que a contribuição precisa cobrir antes de virar lucro.
            'resultado': (contribuicao - fixo_total).quantize(REAL),
            'cobre_o_fixo': contribuicao >= fixo_total,
        }
