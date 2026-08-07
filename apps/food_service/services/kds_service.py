from __future__ import annotations

from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.food_service.models import ItemComanda


class KdsService:
    """Fila da cozinha: avanço de status, prioridade e cancelamento de um item já lançado."""

    _CARIMBO_POR_STATUS = {
        ItemComanda.StatusPreparo.EM_PREPARO: 'iniciado_em',
        ItemComanda.StatusPreparo.PRONTO: 'pronto_em',
        ItemComanda.StatusPreparo.ENTREGUE: 'entregue_em',
    }

    @classmethod
    def avancar_status(cls, *, item: ItemComanda, novo_status: str) -> ItemComanda:
        if novo_status not in ItemComanda.StatusPreparo.values:
            raise DadosInvalidosError('Status de preparo inválido.')
        if item.status_preparo in (ItemComanda.StatusPreparo.ENTREGUE, ItemComanda.StatusPreparo.CANCELADO):
            raise DadosInvalidosError('Item já foi encerrado na cozinha.')

        item.status_preparo = novo_status
        campos = ['status_preparo']
        carimbo = cls._CARIMBO_POR_STATUS.get(novo_status)
        if carimbo and not getattr(item, carimbo):
            setattr(item, carimbo, timezone.now())
            campos.append(carimbo)
        item.save(update_fields=campos)
        return item

    @classmethod
    def alterar_prioridade(cls, *, item: ItemComanda, prioridade: int) -> ItemComanda:
        if prioridade < 0:
            raise DadosInvalidosError('Prioridade não pode ser negativa.')
        item.prioridade = prioridade
        item.save(update_fields=['prioridade'])
        return item

    @classmethod
    def cancelar_item(cls, *, item: ItemComanda) -> ItemComanda:
        if item.status_preparo in (ItemComanda.StatusPreparo.ENTREGUE, ItemComanda.StatusPreparo.CANCELADO):
            raise DadosInvalidosError('Item já foi encerrado na cozinha.')
        item.status_preparo = ItemComanda.StatusPreparo.CANCELADO
        item.save(update_fields=['status_preparo'])
        return item
