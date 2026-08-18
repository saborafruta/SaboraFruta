"""
Gera o contas a receber a partir do pedido de produção.

A regra que sustenta o resto do arquivo é uma só: **a soma das contas
geradas é exatamente o valor total do pedido**. Se a tela mostra R$ 12.400
e o financeiro recebe R$ 12.380, alguém vai passar uma tarde procurando os
R$ 20 — então o arredondamento das parcelas é resolvido aqui dentro, com a
última parcela absorvendo a diferença.

A entrada vira a primeira conta, com vencimento na data do pedido, e não um
pagamento já baixado. O sistema não sabe se o cliente pagou; marcar como
pago seria inventar um recebimento. Ela nasce em aberto e o financeiro dá a
baixa quando o dinheiro entrar de verdade.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.financeiro.constants.enums import StatusContaReceber
from apps.financeiro.models.receber_pagar import ContaReceber

CENTAVO = Decimal('0.01')


@dataclass(frozen=True)
class Parcela:
    """Uma linha do plano de pagamento, antes de virar registro no banco."""
    numero: int
    valor: Decimal
    vencimento: date
    rotulo: str


class FinanceiroPedidoService:

    # Grava no `documento_tipo` da conta. É por ele que o pedido reencontra
    # o que gerou -- e é essa consulta, não um booleano no pedido, que
    # impede gerar duas vezes.
    DOCUMENTO_TIPO = 'pedido_moda'

    # ── Consulta ─────────────────────────────────────────────────────────

    @classmethod
    def contas_do_pedido(cls, pedido):
        """Contas geradas por este pedido que ainda valem (canceladas fora)."""
        return (
            ContaReceber.objects
            .filter(documento_tipo=cls.DOCUMENTO_TIPO, documento_id=pedido.pk)
            .exclude(status=StatusContaReceber.CANCELADO)
            .order_by('parcela')
        )

    # ── Plano de pagamento ───────────────────────────────────────────────

    @classmethod
    def planejar(cls, pedido) -> list[Parcela]:
        """
        Monta o plano sem gravar nada -- é o que a tela mostra na prévia e
        o que o teste consegue conferir sem banco.

        A entrada, quando existe, é a parcela 1. O saldo é dividido pela
        condição de pagamento; sem condição, sai numa parcela só.
        """
        parcelas: list[Parcela] = []
        entrada = pedido.entrada or Decimal('0')
        saldo = pedido.saldo

        if entrada > 0:
            parcelas.append(Parcela(
                numero=1,
                valor=entrada.quantize(CENTAVO),
                vencimento=pedido.data_pedido,
                rotulo='Entrada',
            ))

        if saldo > 0:
            parcelas.extend(cls._parcelar_saldo(pedido, saldo, inicio=len(parcelas)))

        return parcelas

    @classmethod
    def _parcelar_saldo(cls, pedido, saldo: Decimal, inicio: int) -> list[Parcela]:
        cond = pedido.condicao_pagamento
        quantidade = max(1, cond.numero_parcelas) if cond else 1
        intervalo = cond.intervalo_dias if cond else 0
        primeira = cond.dias_primeira_parcela if cond else 0

        # Sem condição de pagamento, o saldo vence na entrega. É o costume da
        # confecção, e evita o efeito colateral bobo de nascer uma conta já
        # vencida no dia seguinte só porque ninguém escolheu a condição.
        base_data = pedido.data_pedido if cond else (
            pedido.data_prevista_entrega or pedido.data_pedido
        )

        # Arredondamento para baixo em todas menos a última: assim a soma
        # nunca ultrapassa o saldo, e a diferença de centavos fica visível
        # numa parcela só, em vez de espalhada.
        valor_base = (saldo / quantidade).quantize(CENTAVO, rounding=ROUND_DOWN)
        distribuido = valor_base * (quantidade - 1)

        parcelas = []
        for i in range(quantidade):
            ultima = i == quantidade - 1
            parcelas.append(Parcela(
                numero=inicio + i + 1,
                valor=(saldo - distribuido) if ultima else valor_base,
                vencimento=base_data + timedelta(days=primeira + i * intervalo),
                rotulo=f'Parcela {i + 1}/{quantidade}',
            ))
        return parcelas

    # ── Geração ──────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def gerar(cls, pedido, usuario=None) -> list[ContaReceber]:
        cls._validar(pedido)

        plano = cls.planejar(pedido)
        if not plano:
            raise DomainError('Não há valor a receber neste pedido.')

        total = len(plano)
        contas = [
            ContaReceber(
                filial=pedido.filial,
                cliente=pedido.cliente,
                documento_tipo=cls.DOCUMENTO_TIPO,
                documento_id=pedido.pk,
                documento_numero=str(pedido.numero),
                parcela=p.numero,
                total_parcelas=total,
                valor_original=p.valor,
                valor_final=p.valor,
                valor_saldo=p.valor,
                data_emissao=pedido.data_pedido,
                data_vencimento=p.vencimento,
                forma_pagamento=pedido.forma_pagamento,
                status=StatusContaReceber.ABERTO,
                observacao=f'Pedido de produção #{pedido.numero:06d} — {p.rotulo}',
                usuario=usuario,
            )
            for p in plano
        ]
        ContaReceber.objects.bulk_create(contas)

        pedido.financeiro_gerado_em = timezone.now()
        pedido.save(update_fields=['financeiro_gerado_em', 'updated_at'])
        return contas

    @classmethod
    def _validar(cls, pedido) -> None:
        if pedido.status == pedido.Status.CANCELADO:
            raise DomainError('Pedido cancelado não gera financeiro.')

        if cls.contas_do_pedido(pedido).exists():
            raise DomainError(
                'Este pedido já tem financeiro gerado. Cancele o financeiro '
                'atual antes de gerar de novo.'
            )

        if pedido.valor_total <= 0:
            raise DomainError(
                'O pedido está sem valor. Preencha o valor unitário dos '
                'produtos antes de gerar o financeiro.'
            )

    # ── Cancelamento ─────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def cancelar(cls, pedido, usuario, motivo: str = '') -> int:
        """
        Desfaz o financeiro do pedido para poder gerar de novo.

        Existe porque a trava de "já gerou" deixaria o usuário preso: quem
        gerasse com o valor errado não teria como corrigir pela tela.

        Conta com qualquer recebimento não é cancelada -- o dinheiro já
        entrou, e apagar isso do financeiro seria pior do que o erro que se
        quer corrigir. Nesse caso o ajuste é no próprio contas a receber.
        """
        contas = list(cls.contas_do_pedido(pedido))
        if not contas:
            raise DomainError('Este pedido não tem financeiro gerado.')

        pagas = [c for c in contas if c.valor_pago > 0]
        if pagas:
            numeros = ', '.join(str(c.parcela) for c in pagas)
            raise DomainError(
                f'Não dá para cancelar: a(s) parcela(s) {numeros} já tem '
                f'recebimento lançado. Ajuste direto no contas a receber.'
            )

        # Import local: o serviço de contas a receber importa modelos do
        # financeiro, e o financeiro não precisa conhecer o moda. Deixar no
        # topo criaria um acoplamento que só existe nesta função.
        from apps.financeiro.services.receber_service import ContaReceberService

        texto = motivo or f'Financeiro do pedido #{pedido.numero:06d} refeito.'
        for conta in contas:
            ContaReceberService.cancelar(conta, texto, usuario)

        pedido.financeiro_gerado_em = None
        pedido.save(update_fields=['financeiro_gerado_em', 'updated_at'])
        return len(contas)
