"""
A cobrança da carga que não nasce de uma venda.

O DINHEIRO QUE NÃO EXISTIA NO SISTEMA

Expedição vinculada a um pedido de venda é cobrada pela venda — é ela que
fatura, emite nota e abre título. Mas nem toda carga nasce de venda: há a que
é montada direto na logística, faturada assim mesmo, e para essa o valor
ficava só na tela do pedido. Ninguém devia nada a ninguém no financeiro, e a
carga saía sem nunca virar cobrança.

QUEM TEM VENDA NÃO COBRA AQUI

Um segundo título sobre a mesma mercadoria é o cliente pagando duas vezes —
e o erro aparece na conciliação bancária, semanas depois, quando já é
constrangimento com o cliente. O serviço recusa, em vez de somar.

A FORMA DE PAGAMENTO DECIDE, COMO NO RESTO DO ERP

`FormaPagamento.gera_parcelas` separa "recebi agora" de "vou receber depois",
e a condição diz em quantas vezes. Repetir aqui uma lista de formas criaria
uma segunda regra, que divergiria da primeira no dia em que alguém cadastrar
uma forma nova.

DINHEIRO NA ENTREGA NÃO VIRA TÍTULO. Conta a receber já quitada enche o
contas a receber de linhas que ninguém precisa cobrar e faz o total em aberto
perder o significado.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.financeiro.constants.enums import StatusContaReceber
from apps.financeiro.models.receber_pagar import ContaReceber
from apps.financeiro.services.parcelamento import ParcelamentoService

ZERO = Decimal('0')
CENTAVOS = Decimal('0.01')

# Como a conta a receber aponta de volta para a expedição.
ORIGEM = 'pedido_expedicao'

# Títulos que já viram dinheiro: cancelar por cima deles esconderia
# recebimento em vez de desfazê-lo.
COM_DINHEIRO = (
    StatusContaReceber.PAGO,
    StatusContaReceber.PAGO_PARCIAL,
)


class FinanceiroExpedicaoService:

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def titulos(pedido):
        """As contas a receber desta expedição."""
        return (
            ContaReceber.objects
            .filter(documento_tipo=ORIGEM, documento_id=pedido.pk)
            .order_by('parcela')
        )

    @classmethod
    def resumo(cls, pedido) -> dict:
        """
        O que a tela precisa dizer sobre o dinheiro desta carga.

        AS TRÊS SITUAÇÕES SÃO DIFERENTES e parecem iguais numa tela silenciosa:
        cobrada pela venda, cobrada aqui, ou não cobrada por ninguém.
        """
        titulos = list(cls.titulos(pedido))
        impedimento = cls.pode_cobrar(pedido)
        return {
            'titulos': titulos,
            'quantidade': len(titulos),
            'valor': sum((t.valor_final or ZERO for t in titulos), ZERO),
            'aberto': sum(
                (
                    t.valor_saldo or ZERO for t in titulos
                    if t.status != StatusContaReceber.CANCELADO
                ),
                ZERO,
            ),
            'cobrada_pela_venda': bool(pedido.pedido_venda_id),
            'impedimento': impedimento,
            'pode_cobrar': not impedimento,
            'a_vista': (
                not pedido.pedido_venda_id
                and pedido.forma_pagamento_id is not None
                and not getattr(pedido.forma_pagamento, 'gera_parcelas', False)
            ),
        }

    @classmethod
    def pode_cobrar(cls, pedido) -> str:
        """Por que esta carga não gera título — vazio quando gera."""
        if pedido.pedido_venda_id:
            return (
                f'Esta carga é do pedido de venda '
                f'{pedido.pedido_venda.numero_pedido} — quem cobra é a venda.'
            )
        if cls.titulos(pedido).exclude(status=StatusContaReceber.CANCELADO).exists():
            return 'Esta carga já tem cobrança lançada.'
        if (pedido.valor_total or ZERO) <= ZERO:
            return 'A carga está sem valor: lance os itens antes de cobrar.'
        forma = pedido.forma_pagamento
        if forma is None:
            return 'Escolha a forma de pagamento no pedido para gerar a cobrança.'
        if not forma.gera_parcelas:
            return (
                f'{forma.descricao} é recebimento na entrega — não abre conta '
                'a receber.'
            )
        return ''

    # ── Escrita ──────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def gerar_titulos(cls, pedido, usuario=None) -> list:
        """Abre as contas a receber desta carga avulsa."""
        impedimento = cls.pode_cobrar(pedido)
        if impedimento:
            raise DadosInvalidosError(impedimento)

        valor = (pedido.valor_total or ZERO).quantize(CENTAVOS)
        emissao = pedido.data_pedido or timezone.localdate()
        parcelas = ParcelamentoService.parcelas(
            valor, emissao,
            condicao=pedido.condicao_pagamento,
            forma=pedido.forma_pagamento,
        )
        total = len(parcelas)

        return [
            ContaReceber.objects.create(
                filial=pedido.filial,
                cliente=pedido.cliente,
                documento_tipo=ORIGEM,
                documento_id=pedido.pk,
                documento_numero=str(pedido.numero),
                parcela=numero,
                total_parcelas=total,
                valor_original=parcela,
                valor_final=parcela,
                valor_saldo=parcela,
                data_emissao=emissao,
                data_vencimento=vencimento,
                forma_pagamento=pedido.forma_pagamento,
                status=StatusContaReceber.ABERTO,
                observacao=(
                    f'Pedido de expedição #{pedido.numero:06d} — '
                    f'{pedido.cliente}.'
                ),
                usuario=usuario or pedido.responsavel,
            )
            for numero, (vencimento, parcela) in enumerate(parcelas, start=1)
        ]

    @classmethod
    def cancelar_titulos(cls, pedido) -> int:
        """
        Cancela a cobrança de uma carga cancelada.

        RECUSA QUANDO JÁ ENTROU DINHEIRO: cancelar por cima de um título pago
        apagaria a cobrança e deixaria o recebimento órfão — o caminho é
        estornar o pagamento primeiro, com quem recebeu respondendo por isso.
        """
        titulos = cls.titulos(pedido).exclude(status=StatusContaReceber.CANCELADO)
        if titulos.filter(status__in=COM_DINHEIRO).exists():
            raise DadosInvalidosError(
                'Esta expedição já tem recebimento lançado no contas a receber. '
                'Estorne o pagamento antes de cancelar.'
            )
        return titulos.update(
            status=StatusContaReceber.CANCELADO, valor_saldo=ZERO,
        )
