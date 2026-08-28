"""
De um valor e uma condição de pagamento, as parcelas.

A MESMA CONTA EM TODO LUGAR

Venda na rua, expedição avulsa, PDV a prazo — todas quebram um valor em
parcelas do mesmo jeito: número de parcelas, intervalo e dias da primeira,
lidos do cadastro da condição. Cada módulo repetir essa aritmética é garantir
que um deles arredonde diferente do outro no dia em que alguém corrigir só um
— e o cliente receba dois boletos que não somam o que ele comprou.

A SOBRA DE CENTAVOS VAI PARA A PRIMEIRA PARCELA

Três parcelas de R$ 100,00 são R$ 33,33 cada, e somam R$ 99,99. O centavo que
falta não pode evaporar: ele é o que faz o cliente ficar devendo um centavo
para sempre, e o título em aberto por R$ 0,01 custa mais para cobrar do que
vale. A primeira parcela leva a diferença porque é a que se confere primeiro.

SEM CONDIÇÃO CADASTRADA É UMA PARCELA

No prazo da forma de pagamento — é o que "a prazo" significa quando ninguém
disse mais nada.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

CENTAVOS = Decimal('0.01')
ZERO = Decimal('0')


class ParcelamentoService:

    @staticmethod
    def numero_de_parcelas(condicao) -> int:
        """Quantas parcelas a condição pede — nunca menos de uma."""
        return max(1, int(getattr(condicao, 'numero_parcelas', 1) or 1))

    @classmethod
    def parcelas(cls, valor, emissao: date, condicao=None, forma=None) -> list[tuple]:
        """
        As parcelas de um valor: [(vencimento, valor), ...].

        `condicao` manda no parcelamento; `forma` só entra quando não há
        condição, para dizer em quantos dias a única parcela vence.
        """
        valor = Decimal(str(valor or 0)).quantize(CENTAVOS)
        if valor <= ZERO:
            return []

        quantidade = cls.numero_de_parcelas(condicao)
        intervalo = int(getattr(condicao, 'intervalo_dias', 30) or 0)
        if condicao is not None:
            primeira = int(getattr(condicao, 'dias_primeira_parcela', 0) or 0)
        else:
            primeira = int(getattr(forma, 'prazo_liquidacao_dias', 0) or 0)

        base = (valor / quantidade).quantize(CENTAVOS, rounding=ROUND_HALF_UP)
        parcelas = []
        for indice in range(quantidade):
            # A PRIMEIRA LEVA A SOBRA: base × (n-1) é o que as outras somam,
            # e o que resta é dela.
            parcela = base if indice else valor - base * (quantidade - 1)
            parcelas.append((
                emissao + timedelta(days=primeira + intervalo * indice),
                parcela.quantize(CENTAVOS),
            ))
        return parcelas
