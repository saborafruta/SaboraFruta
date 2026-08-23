"""
Margens — o que sobrou de cada pedido e de cada produto.

CUSTO SEM PREÇO NÃO DECIDE NADA. A tela de Custos diz quanto a ordem
consumiu; esta diz se o que foi cobrado cobriu aquilo. É a única do vertical
em que os dois lados da conta aparecem juntos, e é por ela que se descobre o
produto que a fábrica faz bem e vende barato demais.

O CUSTO VEM PRONTO DE `CustoRealService`, e não recalculado aqui. Duas
implementações do mesmo custo divergem, e aí Custos e Margens dariam números
diferentes para a mesma ordem — o tipo de discordância que faz as duas telas
perderem a confiança de uma vez. Tudo o que este serviço acrescenta é o lado
da receita.

FRETE FICA DE FORA DA RECEITA. Ele é repasse, e o custo dele não está do
outro lado da conta: somá-lo inventaria margem que não existe. DESCONTO
FICA DENTRO — ele é redução de receita de verdade, e é rateado entre as
ordens do pedido na proporção do que cada uma vale, porque o desconto é do
pedido e a margem é medida por ordem.

MARGEM PREVISTA E MARGEM REAL LADO A LADO. A prevista usa o custo da ficha,
a real usa o que a ordem custou. A diferença entre as duas não é detalhe: é
o quanto a fábrica comeu do lucro que o comercial vendeu. Um pedido pode ter
sido bem precificado e ainda assim dar prejuízo.

PEDIDO SEM PREÇO NÃO É PREJUÍZO. Amostra, reposição de garantia e orçamento
ainda não valorado entram com receita zero, e tratá-los como margem de −100%
afundaria o número de todo mundo. Eles ficam de fora das médias e são
contados à parte.
"""
from decimal import Decimal

from .custo_real import PERIODOS, CustoRealService  # noqa: F401 (PERIODOS é reexportado)

ZERO = Decimal('0')
CEM = Decimal('100')
CENTAVO = Decimal('0.01')

# Abaixo disto o pedido não paga a estrutura da casa. Não é a margem alvo da
# empresa -- é o ponto em que vale olhar o pedido antes de repetir o preço.
MARGEM_MAGRA = Decimal('15')


def _pct(numerador, denominador):
    """Percentual, ou None quando não há base — que NÃO é o mesmo que 0%."""
    if not denominador:
        return None
    return (Decimal(numerador) / Decimal(denominador) * CEM).quantize(Decimal('0.1'))


class MargemService:
    """Receita contra custo, por ordem, por pedido e por produto."""

    @classmethod
    def painel(cls, filial, dias: int) -> dict:
        custos = CustoRealService.painel(filial, dias)
        linhas = [cls._com_receita(l) for l in custos['linhas']]
        cls._ratear_descontos(linhas)
        for linha in linhas:
            cls._fechar(linha)

        # Da PIOR margem para a melhor: a tela existe para achar o que está
        # sendo vendido barato demais, e isso tem de estar na primeira linha.
        com_preco = [l for l in linhas if not l['sem_preco']]
        com_preco.sort(key=lambda l: l['margem_pct'])
        sem_preco = [l for l in linhas if l['sem_preco']]

        return {
            'desde': custos['desde'],
            'linhas': com_preco + sem_preco,
            'por_pedido': cls._por_pedido(linhas),
            'por_produto': cls._por_produto(linhas),
            'resumo': cls._resumo(linhas),
            'magra': MARGEM_MAGRA,
        }

    # ── Receita ──────────────────────────────────────────────────────────

    @staticmethod
    def _com_receita(linha) -> dict:
        """
        Preço do item × quantidade DESTA ordem.

        Pela quantidade da ordem e não do item: um item partido em duas
        ordens divide a receita entre elas, e somar o item inteiro em cada
        uma dobraria o faturamento do pedido.
        """
        ordem = linha['ordem']
        item = ordem.item
        preco = (item.valor_unitario or ZERO) if item else ZERO
        linha = dict(linha)
        linha['preco_unitario'] = preco
        linha['receita_bruta'] = (preco * ordem.quantidade).quantize(CENTAVO)
        linha['desconto'] = ZERO
        return linha

    @staticmethod
    def _ratear_descontos(linhas) -> None:
        """
        O desconto é do PEDIDO e a margem é medida por ORDEM.

        Rateado na proporção do que cada ordem vale: dar o desconto inteiro
        à primeira faria uma ordem parecer péssima e as outras ótimas, sem
        que nada disso tenha acontecido na fábrica.
        """
        por_pedido: dict[int, list] = {}
        for linha in linhas:
            pedido = linha['ordem'].pedido
            if pedido is not None:
                por_pedido.setdefault(pedido.pk, []).append(linha)

        for grupo in por_pedido.values():
            pedido = grupo[0]['ordem'].pedido
            desconto = pedido.desconto or ZERO
            if desconto <= ZERO:
                continue
            base = sum((l['receita_bruta'] for l in grupo), ZERO)
            if base <= ZERO:
                continue
            # O desconto do pedido pode cobrir itens que não entraram na
            # janela; ratear só sobre os presentes exageraria o corte. O
            # teto é o próprio subtotal do pedido.
            subtotal = pedido.subtotal or base
            proporcao = min(base / subtotal, Decimal('1')) if subtotal else Decimal('1')
            aplicavel = desconto * proporcao
            for linha in grupo:
                fatia = aplicavel * (linha['receita_bruta'] / base)
                linha['desconto'] = fatia.quantize(CENTAVO)

    @staticmethod
    def _fechar(linha) -> None:
        receita = (linha['receita_bruta'] - linha['desconto']).quantize(CENTAVO)
        linha['receita'] = receita
        # Amostra, garantia e orçamento sem preço: sem receita não há margem
        # a medir, e -100% afundaria a média de todo mundo.
        linha['sem_preco'] = receita <= ZERO
        linha['margem'] = (receita - linha['real']).quantize(CENTAVO)
        linha['margem_prevista'] = (receita - linha['previsto']).quantize(CENTAVO)
        linha['margem_pct'] = _pct(linha['margem'], receita) or ZERO
        linha['margem_prevista_pct'] = _pct(linha['margem_prevista'], receita) or ZERO
        # O quanto a fábrica comeu do lucro que o comercial vendeu.
        linha['erosao'] = (linha['margem_prevista'] - linha['margem']).quantize(CENTAVO)
        linha['prejuizo'] = not linha['sem_preco'] and linha['margem'] < ZERO
        linha['magra'] = (
            not linha['sem_preco'] and ZERO <= linha['margem_pct'] < MARGEM_MAGRA
        )

    # ── Agrupamentos ─────────────────────────────────────────────────────

    @classmethod
    def _por_pedido(cls, linhas) -> list[dict]:
        grupos: dict[int, dict] = {}
        for linha in linhas:
            pedido = linha['ordem'].pedido
            if pedido is None:
                continue
            grupo = grupos.setdefault(pedido.pk, {
                'pedido': pedido,
                'numero': pedido.numero,
                'cliente': (
                    pedido.cliente.razao_social if pedido.cliente_id else '—'
                ),
                'ordens': 0,
                'pecas': 0,
                'receita': ZERO,
                'custo': ZERO,
                'previsto': ZERO,
            })
            grupo['ordens'] += 1
            grupo['pecas'] += linha['boas']
            grupo['receita'] += linha['receita']
            grupo['custo'] += linha['real']
            grupo['previsto'] += linha['previsto']
        return cls._fechar_grupos(grupos.values(), chave='numero')

    @classmethod
    def _por_produto(cls, linhas) -> list[dict]:
        grupos: dict[int, dict] = {}
        for linha in linhas:
            item = linha['ordem'].item
            produto = item.produto if item else None
            # Sem produto ligado, o nome do item é o que há -- e itens
            # avulsos com a mesma descrição são o mesmo produto para quem lê.
            chave = produto.pk if produto else f't{linha["produto"]}'
            grupo = grupos.setdefault(chave, {
                'produto': produto,
                'nome': produto.nome if produto else linha['produto'],
                'codigo': produto.codigo if produto else '',
                'ordens': 0,
                'pecas': 0,
                'receita': ZERO,
                'custo': ZERO,
                'previsto': ZERO,
            })
            grupo['ordens'] += 1
            grupo['pecas'] += linha['boas']
            grupo['receita'] += linha['receita']
            grupo['custo'] += linha['real']
            grupo['previsto'] += linha['previsto']
        return cls._fechar_grupos(grupos.values(), chave='nome')

    @staticmethod
    def _fechar_grupos(grupos, chave) -> list[dict]:
        fechados = []
        for grupo in grupos:
            receita = grupo['receita'].quantize(CENTAVO)
            grupo['receita'] = receita
            grupo['custo'] = grupo['custo'].quantize(CENTAVO)
            grupo['previsto'] = grupo['previsto'].quantize(CENTAVO)
            grupo['margem'] = (receita - grupo['custo']).quantize(CENTAVO)
            grupo['margem_prevista'] = (receita - grupo['previsto']).quantize(CENTAVO)
            grupo['sem_preco'] = receita <= ZERO
            grupo['margem_pct'] = _pct(grupo['margem'], receita) or ZERO
            grupo['margem_prevista_pct'] = (
                _pct(grupo['margem_prevista'], receita) or ZERO
            )
            grupo['prejuizo'] = not grupo['sem_preco'] and grupo['margem'] < ZERO
            grupo['magra'] = (
                not grupo['sem_preco'] and ZERO <= grupo['margem_pct'] < MARGEM_MAGRA
            )
            grupo['por_peca'] = (
                (grupo['margem'] / grupo['pecas']).quantize(CENTAVO)
                if grupo['pecas'] else None
            )
            fechados.append(grupo)
        # Pior margem primeiro; sem preço no fim, onde não atrapalham a
        # leitura de quem procura o que está barato demais.
        return sorted(fechados, key=lambda g: (
            g['sem_preco'], g['margem_pct'], g[chave],
        ))

    # ── Cabeçalho ────────────────────────────────────────────────────────

    @staticmethod
    def _resumo(linhas) -> dict:
        com_preco = [l for l in linhas if not l['sem_preco']]
        receita = sum((l['receita'] for l in com_preco), ZERO)
        custo = sum((l['real'] for l in com_preco), ZERO)
        previsto = sum((l['previsto'] for l in com_preco), ZERO)
        prejuizo = [l for l in com_preco if l['prejuizo']]
        return {
            'ordens': len(linhas),
            'receita': receita.quantize(CENTAVO),
            'custo': custo.quantize(CENTAVO),
            'margem': (receita - custo).quantize(CENTAVO),
            'margem_pct': _pct(receita - custo, receita),
            'margem_prevista_pct': _pct(receita - previsto, receita),
            # A erosão é o que a fábrica comeu do lucro vendido: é a ponte
            # entre esta tela e a de Custos.
            'erosao': (custo - previsto).quantize(CENTAVO),
            'prejuizo': len(prejuizo),
            'valor_prejuizo': sum(
                (l['margem'] for l in prejuizo), ZERO,
            ).quantize(CENTAVO),
            'magras': sum(1 for l in com_preco if l['magra']),
            # A pior é a de menor margem PERCENTUAL: um pedido grande com
            # margem fina pode perder mais em reais e ainda assim estar
            # melhor precificado que um pequeno vendido no prejuízo.
            'pior': min(
                com_preco, key=lambda l: l['margem_pct'], default=None,
            ),
            'sem_preco': sum(1 for l in linhas if l['sem_preco']),
            'estimadas': sum(1 for l in linhas if l['estimado']),
        }
