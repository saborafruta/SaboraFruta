"""
O pedido de expedição vira entrega no romaneio.

O QUE SE REDIGITAVA

Vincular o pedido a um romaneio guardava um ponteiro e mais nada: a entrega —
cliente, documento, endereço, volumes, peso e valor — era digitada de novo na
tela do romaneio, com o pedido ao lado sabendo tudo. Além do tempo, é assim
que o papel do motorista acaba divergindo do pedido: alguém corrige o
endereço num lugar só, e a carga vai para a rua errada.

A ENTREGA É DO PEDIDO, E ACOMPANHA O PEDIDO

Tirar o pedido do romaneio tira a entrega junto; mudar de romaneio move a
entrega. Deixá-la para trás produziria uma entrega órfã que o motorista
levaria sem ter o que entregar — e o romaneio somaria peso e valor de carga
que não está no caminhão.

O QUE JÁ SAIU NÃO SE MEXE

Entrega marcada como carregada ou entregue é fato registrado: se o pedido for
desvinculado depois disso, a entrega fica, e o vínculo é que se desfaz.
Apagar o registro de uma entrega feita é apagar a prova de que ela aconteceu.

O QUE ELE NÃO FAZ

Não cria itens produto a produto no romaneio: o romaneio é rota de entrega —
uma linha por parada, com o total daquela carga. O detalhe do que vai dentro
é do pedido de expedição, e repetir as linhas nos dois faria duas listas para
conferir.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.logistica.models import ItemRomaneioCarga

ZERO = Decimal('0')

# Entrega que já aconteceu no mundo: o registro dela não se desfaz.
JA_ACONTECEU = (
    ItemRomaneioCarga.StatusEntrega.CARREGADO,
    ItemRomaneioCarga.StatusEntrega.ENTREGUE,
    ItemRomaneioCarga.StatusEntrega.NAO_ENTREGUE,
)


class RomaneioDoPedidoService:

    @staticmethod
    def entrega_do_pedido(pedido):
        """A entrega que este pedido gerou, se ainda existe."""
        return (
            ItemRomaneioCarga.objects
            .filter(pedido_expedicao=pedido)
            .select_related('romaneio')
            .first()
        )

    @classmethod
    @transaction.atomic
    def sincronizar(cls, pedido) -> dict:
        """
        Põe, move ou tira a entrega do pedido, conforme o romaneio dele.

        CHAMADO SEMPRE QUE O PEDIDO É SALVO, e não por um botão: entrega que
        depende de alguém lembrar de clicar é entrega que falta no dia em que
        a pessoa estava com pressa.
        """
        entrega = cls.entrega_do_pedido(pedido)
        romaneio = pedido.romaneio

        if romaneio is None:
            return cls._desvincular(entrega)

        if entrega is None:
            return cls._criar(pedido, romaneio)

        if entrega.romaneio_id != romaneio.pk:
            return cls._mover(pedido, entrega, romaneio)

        return cls._atualizar(pedido, entrega)

    # ── Cada caso ────────────────────────────────────────────────────────

    @classmethod
    def _criar(cls, pedido, romaneio) -> dict:
        entrega = ItemRomaneioCarga.objects.create(
            romaneio=romaneio,
            pedido_expedicao=pedido,
            pedido_venda=pedido.pedido_venda,
            ordem=romaneio.itens.count() + 1,
            **cls._dados(pedido),
        )
        romaneio.recalcular_totais()
        return {'acao': 'criada', 'entrega': entrega, 'romaneio': romaneio}

    @classmethod
    def _mover(cls, pedido, entrega, romaneio) -> dict:
        anterior = entrega.romaneio
        if entrega.status_entrega in JA_ACONTECEU:
            # A CARGA JA' FOI: a entrega fica onde aconteceu, e o pedido
            # segue para o romaneio novo sem levar o registro antigo junto.
            entrega.pedido_expedicao = None
            entrega.save(update_fields=['pedido_expedicao', 'updated_at'])
            return cls._criar(pedido, romaneio)

        entrega.romaneio = romaneio
        entrega.ordem = romaneio.itens.count() + 1
        for campo, valor in cls._dados(pedido).items():
            setattr(entrega, campo, valor)
        entrega.save()
        anterior.recalcular_totais()
        romaneio.recalcular_totais()
        return {'acao': 'movida', 'entrega': entrega, 'romaneio': romaneio}

    @classmethod
    def _atualizar(cls, pedido, entrega) -> dict:
        """
        O pedido mudou — a entrega acompanha.

        SÓ ENQUANTO ELA NÃO ACONTECEU: mexer no endereço de uma entrega já
        feita reescreveria o que o motorista encontrou.
        """
        if entrega.status_entrega in JA_ACONTECEU:
            return {'acao': 'mantida', 'entrega': entrega, 'romaneio': entrega.romaneio}

        for campo, valor in cls._dados(pedido).items():
            setattr(entrega, campo, valor)
        entrega.save()
        entrega.romaneio.recalcular_totais()
        return {'acao': 'atualizada', 'entrega': entrega, 'romaneio': entrega.romaneio}

    @classmethod
    def _desvincular(cls, entrega) -> dict:
        if entrega is None:
            return {'acao': 'nenhuma', 'entrega': None, 'romaneio': None}

        romaneio = entrega.romaneio
        if entrega.status_entrega in JA_ACONTECEU:
            # APAGAR O REGISTRO DE UMA ENTREGA FEITA e' apagar a prova de que
            # ela aconteceu. O vinculo se desfaz; a linha fica.
            entrega.pedido_expedicao = None
            entrega.save(update_fields=['pedido_expedicao', 'updated_at'])
            return {'acao': 'soltada', 'entrega': entrega, 'romaneio': romaneio}

        entrega.delete()
        romaneio.recalcular_totais()
        return {'acao': 'removida', 'entrega': None, 'romaneio': romaneio}

    # ── O que a entrega copia do pedido ──────────────────────────────────

    @staticmethod
    def _dados(pedido) -> dict:
        """
        Cliente, endereço e totais — copiados, e não apontados.

        O ROMANEIO É O PAPEL DO DIA. Ele registra para onde a carga foi
        naquela viagem; se o cadastro do cliente mudar de endereço depois, a
        entrega feita continua dizendo onde ela foi feita.
        """
        endereco = dict(pedido.endereco_entrega or {})
        return {
            'cliente_nome': str(pedido.cliente),
            'documento': getattr(pedido.cliente, 'cpf_cnpj', '') or '',
            'endereco_entrega': endereco,
            'volumes': pedido.volumes or ZERO,
            'peso_kg': pedido.peso_total_kg or ZERO,
            'valor': pedido.valor_total or ZERO,
            'observacao': pedido.observacao or '',
        }
