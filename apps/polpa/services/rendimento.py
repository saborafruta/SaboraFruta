"""
Rendimento real: o que a fruta rendeu contra o que ela deveria render.

O NÚMERO QUE PAGA A CONTA. Dois pontos abaixo do esperado numa fábrica que
processa 40 toneladas por dia são 800 kg de polpa que alguém comprou,
descascou, pagou e não vendeu. É a diferença que não aparece em lugar
nenhum do balanço até virar prejuízo do mês.

NENHUM NÚMERO NASCE AQUI. O esperado está na receita (seção 2) e na ficha
da fruta (seção 1); o realizado está nas ordens encerradas, que já guardam
peso de entrada e peso de saída. Este serviço só põe os dois lado a lado --
que é exatamente o que ninguém faz de cabeça no fim do dia.

PESO CONTRA PESO, e não produzido contra planejado. "Cumpri a meta?" é
outra pergunta, e ela já tem tela. Rendimento de polpa é quanto da FRUTA
virou PRODUTO: 1.000 kg de manga que viram 600 kg de polpa rendem 60%,
independentemente de a ordem ter pedido 500 ou 5.000.

A ATRIBUIÇÃO POR FRUTA É HONESTA OU NÃO EXISTE. Uma receita que consome
duas frutas não tem como dizer qual das duas rendeu mal -- os dois pesos
entram misturados no mesmo tanque. Em vez de repartir por chute, essas
receitas ficam de fora do quadro por fruta, e a tela diz quantas ficaram.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.polpa.models import FichaProduto, OrdemPolpa, Receita
from apps.producao.models import ItemFichaTecnica, OrdemProducao

ZERO = Decimal('0')
CEM = Decimal('100')


class RendimentoService:

    JANELA = 90

    # ── Base ─────────────────────────────────────────────────────────────

    @staticmethod
    def _ordens(filial, dias: int):
        """
        As ordens encerradas da janela, com peso dos dois lados.

        Ordem sem os dois pesos fica de fora: sem entrada ou sem saída não
        há rendimento a calcular, e assumir zero num dos lados inventaria
        uma perda de 100% que ninguém teve.
        """
        desde = timezone.now() - timedelta(days=dias)
        return [
            op for op in (
                OrdemPolpa.objects.for_filial(filial)
                .filter(ordem__status=OrdemProducao.Status.ENCERRADA)
                .select_related(
                    'ordem', 'ordem__produto_acabado', 'ordem__ficha_tecnica',
                    'receita', 'receita__ficha',
                )
            )
            if op.ordem.data_fim_real and op.ordem.data_fim_real >= desde
            and (op.ordem.peso_entrada_mp or ZERO) > ZERO
            and op.ordem.peso_saida_produzido is not None
        ]

    @staticmethod
    def _percentual(entrada, saida):
        if not entrada or entrada <= ZERO:
            return None
        return (saida / entrada * CEM).quantize(Decimal('0.01'))

    @classmethod
    def _linha(cls, esperado, entrada, saida, ordens: int) -> dict:
        """
        Esperado, real, desvio — e o que o desvio custou em quilo.

        O DESVIO EM QUILO É O QUE CONVENCE. "Menos 2,3 pontos" é abstrato;
        "1.150 kg de fruta que não viraram polpa" é a conversa que a fábrica
        tem de verdade.
        """
        real = cls._percentual(entrada, saida)
        desvio = None
        kg_perdidos = None
        if real is not None and esperado:
            desvio = (real - esperado).quantize(Decimal('0.01'))
            if desvio < ZERO:
                kg_perdidos = (entrada * -desvio / CEM).quantize(Decimal('0.001'))
        return {
            'esperado': esperado or None,
            'real': real,
            'desvio': desvio,
            'kg_perdidos': kg_perdidos,
            'entrada': entrada,
            'saida': saida,
            'ordens': ordens,
            # SEM RÉGUA NÃO É "NO ALVO": é receita ou fruta sem rendimento
            # cadastrado, e a tela cobra o cadastro em vez de aprovar.
            'sem_esperado': not esperado,
            'abaixo': bool(desvio is not None and desvio < ZERO),
        }

    # ── Os três recortes ─────────────────────────────────────────────────

    @classmethod
    def por_produto(cls, filial, dias: int | None = None) -> list[dict]:
        """Cada produto acabado com o rendimento da sua receita."""
        ordens = cls._ordens(filial, dias or cls.JANELA)

        grupos: dict = {}
        for op in ordens:
            chave = op.ordem.produto_acabado_id
            grupo = grupos.setdefault(chave, {
                'produto': op.ordem.produto_acabado,
                'receita': op.receita,
                'entrada': ZERO, 'saida': ZERO, 'ordens': 0,
            })
            grupo['entrada'] += op.ordem.peso_entrada_mp or ZERO
            grupo['saida'] += op.ordem.peso_saida_produzido or ZERO
            grupo['ordens'] += 1

        linhas = []
        for grupo in grupos.values():
            receita = grupo['receita']
            linhas.append({
                'produto': grupo['produto'],
                'receita': receita,
                **cls._linha(
                    getattr(receita, 'rendimento_esperado', None),
                    grupo['entrada'], grupo['saida'], grupo['ordens'],
                ),
            })

        # O PIOR PRIMEIRO: quem abre esta tela quer saber onde está
        # perdendo, não ler a lista inteira em ordem alfabética.
        linhas.sort(key=lambda l: (
            l['desvio'] is None, l['desvio'] if l['desvio'] is not None else ZERO,
        ))
        return linhas

    @classmethod
    def _fruta_da_receita(cls, receita) -> tuple:
        """
        A fruta de uma receita — e só quando é uma só.

        Duas frutas no mesmo tanque não têm como ser separadas depois: os
        dois pesos entraram juntos. Repartir por chute daria um número com
        cara de medição.
        """
        if receita is None:
            return None, 0
        produtos = ItemFichaTecnica.objects.filter(
            ficha=receita.ficha,
        ).values_list('materia_prima_id', flat=True)
        frutas = set(
            FichaProduto.objects
            .filter(produto_id__in=list(produtos), fruta__isnull=False)
            .values_list('fruta_id', flat=True)
        )
        if len(frutas) != 1:
            return None, len(frutas)
        return frutas.pop(), 1

    @classmethod
    def por_fruta(cls, filial, dias: int | None = None) -> dict:
        """
        Cada fruta com o que ela rendeu contra a régua do cadastro dela.

        Devolve também quantas ordens ficaram de fora, e por quê -- número
        que aparece sem explicação é número em que ninguém confia.
        """
        from apps.polpa.models import Fruta

        ordens = cls._ordens(filial, dias or cls.JANELA)
        cache: dict = {}
        grupos: dict = {}
        misturadas = 0
        sem_fruta = 0

        for op in ordens:
            receita_id = op.receita_id
            if receita_id not in cache:
                cache[receita_id] = cls._fruta_da_receita(op.receita)
            fruta_id, quantas = cache[receita_id]

            if fruta_id is None:
                if quantas > 1:
                    misturadas += 1
                else:
                    sem_fruta += 1
                continue

            grupo = grupos.setdefault(fruta_id, {
                'entrada': ZERO, 'saida': ZERO, 'ordens': 0,
            })
            grupo['entrada'] += op.ordem.peso_entrada_mp or ZERO
            grupo['saida'] += op.ordem.peso_saida_produzido or ZERO
            grupo['ordens'] += 1

        frutas = {
            f.pk: f for f in Fruta.objects.for_filial(filial)
            .filter(pk__in=list(grupos))
        }
        linhas = [
            {
                'fruta': frutas[fruta_id],
                **cls._linha(
                    frutas[fruta_id].rendimento_esperado,
                    grupo['entrada'], grupo['saida'], grupo['ordens'],
                ),
            }
            for fruta_id, grupo in grupos.items() if fruta_id in frutas
        ]
        linhas.sort(key=lambda l: (
            l['desvio'] is None, l['desvio'] if l['desvio'] is not None else ZERO,
        ))
        return {
            'linhas': linhas,
            'misturadas': misturadas,
            'sem_fruta': sem_fruta,
        }

    @classmethod
    def por_ordem(cls, filial, dias: int | None = None,
                  limite: int = 60) -> list[dict]:
        """Batida por batida, da mais recente para a mais antiga."""
        ordens = cls._ordens(filial, dias or cls.JANELA)
        ordens.sort(key=lambda op: op.ordem.data_fim_real, reverse=True)

        return [
            {
                'op': op,
                'produto': op.ordem.produto_acabado,
                'quando': op.ordem.data_fim_real,
                **cls._linha(
                    getattr(op.receita, 'rendimento_esperado', None),
                    op.ordem.peso_entrada_mp or ZERO,
                    op.ordem.peso_saida_produzido or ZERO,
                    1,
                ),
            }
            for op in ordens[:limite]
        ]

    # ── O topo da tela ───────────────────────────────────────────────────

    @classmethod
    def resumo(cls, filial, dias: int | None = None) -> dict:
        """
        O rendimento da fábrica inteira na janela.

        `None` sem ordem nenhuma: zero seria lido como "a fábrica não rendeu
        nada", e uma fábrica que ainda não fechou ordem apareceria em
        colapso.
        """
        ordens = cls._ordens(filial, dias or cls.JANELA)
        entrada = sum((op.ordem.peso_entrada_mp or ZERO for op in ordens), ZERO)
        saida = sum((op.ordem.peso_saida_produzido or ZERO for op in ordens), ZERO)

        # O ESPERADO DA FÁBRICA É PONDERADO PELO PESO, não uma média das
        # receitas: uma receita rodada uma vez não pode pesar o mesmo que a
        # que roda todo dia.
        esperado_kg = ZERO
        base = ZERO
        for op in ordens:
            esperado = getattr(op.receita, 'rendimento_esperado', None)
            if not esperado:
                continue
            peso = op.ordem.peso_entrada_mp or ZERO
            esperado_kg += peso * esperado / CEM
            base += peso

        esperado = (
            (esperado_kg / base * CEM).quantize(Decimal('0.01'))
            if base > ZERO else None
        )

        linha = cls._linha(esperado, entrada, saida, len(ordens))
        linha['sem_regua'] = sum(
            1 for op in ordens
            if not getattr(op.receita, 'rendimento_esperado', None)
        )
        return linha

    @classmethod
    def painel(cls, filial, dias: int | None = None) -> dict:
        dias = dias or cls.JANELA
        por_fruta = cls.por_fruta(filial, dias)
        return {
            'dias': dias,
            'resumo': cls.resumo(filial, dias),
            'produtos': cls.por_produto(filial, dias),
            'frutas': por_fruta['linhas'],
            'misturadas': por_fruta['misturadas'],
            'sem_fruta': por_fruta['sem_fruta'],
            'ordens': cls.por_ordem(filial, dias),
        }
