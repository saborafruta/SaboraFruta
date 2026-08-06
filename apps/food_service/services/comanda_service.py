from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.food_service.models import Comanda, ItemComanda, Mesa
from apps.pdv.services.venda_pdv_service import VendaPDVService


class ComandaService:
    """
    Ciclo de vida de uma comanda: abrir, lançar/remover/transferir itens,
    unir/dividir mesas ou comandas, e fechar convergindo para uma VendaPDV.
    """

    @classmethod
    @transaction.atomic
    def abrir(
        cls,
        *,
        filial,
        usuario,
        mesas: list[Mesa] | None = None,
        cliente=None,
        nome_ocupante: str = '',
        garcom=None,
        quantidade_pessoas: int = 1,
    ) -> Comanda:
        comanda = Comanda.objects.create(
            filial=filial,
            cliente=cliente,
            nome_ocupante=nome_ocupante,
            garcom=garcom,
            quantidade_pessoas=quantidade_pessoas or 1,
        )
        if mesas:
            comanda.mesas.set(mesas)
            Mesa.objects.filter(pk__in=[m.pk for m in mesas]).update(status=Mesa.Status.OCUPADA)
        return comanda

    @classmethod
    def adicionar_item(cls, *, comanda: Comanda, produto, quantidade, observacoes: str = '') -> ItemComanda:
        if comanda.status != Comanda.Status.ABERTA:
            raise DadosInvalidosError('Comanda não está aberta.')
        quantidade = Decimal(str(quantidade))
        if quantidade <= 0:
            raise DadosInvalidosError('Quantidade deve ser positiva.')
        preco_info = VendaPDVService.resolver_preco_produto(
            produto, comanda.filial, quantidade, cliente=comanda.cliente,
        )
        return ItemComanda.objects.create(
            comanda=comanda,
            produto=produto,
            quantidade=quantidade,
            valor_unitario=preco_info['preco'],
            observacoes=observacoes,
        )

    @classmethod
    def remover_item(cls, *, item: ItemComanda):
        if item.comanda.status != Comanda.Status.ABERTA:
            raise DadosInvalidosError('Comanda não está aberta.')
        item.delete()

    @classmethod
    def transferir_item(cls, *, item: ItemComanda, destino: Comanda) -> ItemComanda:
        if destino.status != Comanda.Status.ABERTA:
            raise DadosInvalidosError('Comanda de destino não está aberta.')
        item.comanda = destino
        item.save(update_fields=['comanda'])
        return item

    @classmethod
    @transaction.atomic
    def unir_comandas(cls, *, origem: Comanda, destino: Comanda):
        if origem.pk == destino.pk:
            raise DadosInvalidosError('Selecione comandas diferentes para unir.')
        for item in list(origem.itens.all()):
            cls.transferir_item(item=item, destino=destino)
        origem.status = Comanda.Status.CANCELADA
        origem.save(update_fields=['status'])

    @classmethod
    @transaction.atomic
    def transferir_mesa(cls, *, comanda: Comanda, mesa_origem: Mesa, mesa_destino: Mesa):
        comanda.mesas.remove(mesa_origem)
        comanda.mesas.add(mesa_destino)
        mesa_destino.status = Mesa.Status.OCUPADA
        mesa_destino.save(update_fields=['status'])
        if not mesa_origem.comandas.filter(status=Comanda.Status.ABERTA).exists():
            mesa_origem.status = Mesa.Status.LIVRE
            mesa_origem.save(update_fields=['status'])

    @classmethod
    @transaction.atomic
    def unir_mesas(cls, *, comanda: Comanda, mesas_adicionais: list[Mesa]):
        comanda.mesas.add(*mesas_adicionais)
        Mesa.objects.filter(pk__in=[m.pk for m in mesas_adicionais]).update(status=Mesa.Status.OCUPADA)

    @classmethod
    @transaction.atomic
    def fechar(
        cls,
        *,
        comanda: Comanda,
        request,
        pagamentos: list[dict],
        desconto=Decimal('0'),
        acrescimo=Decimal('0'),
    ):
        from apps.pdv.views.pdv import _sessao_aberta

        if comanda.status != Comanda.Status.ABERTA:
            raise DadosInvalidosError('Comanda não está aberta.')

        itens_comanda = list(comanda.itens.all())
        if not itens_comanda:
            raise DadosInvalidosError('Comanda sem itens lançados.')

        sessao = _sessao_aberta(request)
        if not sessao:
            raise DadosInvalidosError('Nenhuma sessão de caixa aberta.')

        itens = [
            {'produto_id': item.produto_id, 'quantidade': item.quantidade}
            for item in itens_comanda
        ]

        venda = VendaPDVService.finalizar_venda(
            sessao=sessao,
            filial=comanda.filial,
            usuario=request.user,
            itens=itens,
            pagamentos=pagamentos,
            cliente_id=comanda.cliente_id,
            desconto=desconto,
            acrescimo=acrescimo,
            observacao=comanda.observacoes,
            request=request,
        )

        comanda.venda_pdv = venda
        comanda.status = Comanda.Status.FECHADA
        comanda.fechada_em = timezone.now()
        comanda.save(update_fields=['venda_pdv', 'status', 'fechada_em'])

        for mesa in comanda.mesas.all():
            if not mesa.comandas.filter(status=Comanda.Status.ABERTA).exists():
                mesa.status = Mesa.Status.AGUARDANDO_LIMPEZA
                mesa.save(update_fields=['status'])

        return venda
