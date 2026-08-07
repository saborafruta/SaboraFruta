from __future__ import annotations

from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.food_service.models import ItemComanda
from apps.food_service.services.notificacao_service import (
    notificar_item_cancelado,
    notificar_pedido_entregue,
    notificar_pedido_iniciado,
    notificar_pedido_pronto,
)


class KdsService:
    """Fila da cozinha: avanço de status, prioridade e cancelamento de um item já lançado."""

    _CARIMBO_POR_STATUS = {
        ItemComanda.StatusPreparo.EM_PREPARO: 'iniciado_em',
        ItemComanda.StatusPreparo.PRONTO: 'pronto_em',
        ItemComanda.StatusPreparo.ENTREGUE: 'entregue_em',
    }

    _NOTIFICAR_POR_STATUS = {
        ItemComanda.StatusPreparo.EM_PREPARO: notificar_pedido_iniciado,
        ItemComanda.StatusPreparo.PRONTO: notificar_pedido_pronto,
        ItemComanda.StatusPreparo.ENTREGUE: notificar_pedido_entregue,
    }

    # Quando o produto não tem tempo de preparo cadastrado, assume este valor
    # só para dar um prazo pra ordenar -- não é exibido como estimativa real.
    TEMPO_PREPARO_PADRAO_MINUTOS = 15

    @classmethod
    def prazo(cls, item: ItemComanda):
        """
        "Devido em" -- horário de recebimento + tempo de preparo esperado do
        produto. Itens com prazo mais próximo (vão atrasar primeiro se não
        começarem agora) sobem na fila, no mesmo espírito de escalonamento
        por menor prazo (earliest due date) usado em cozinhas de verdade:
        um prato de preparo longo recebido há pouco pode já estar mais
        urgente que um prato rápido recebido antes dele.
        """
        referencia = item.recebido_em or item.adicionado_em
        tempo_preparo = item.produto.tempo_preparo_minutos or cls.TEMPO_PREPARO_PADRAO_MINUTOS
        return referencia + timezone.timedelta(minutes=tempo_preparo)

    @classmethod
    def fila_ordenada(cls, itens):
        """
        Prioridade manual sempre vence (é a forma do gerente furar a fila);
        dentro do mesmo nível de prioridade, ordena por prazo mais próximo.
        """
        return sorted(itens, key=lambda item: (-item.prioridade, cls.prazo(item)))

    @classmethod
    def avancar_status(cls, *, item: ItemComanda, novo_status: str, usuario=None) -> ItemComanda:
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
        if novo_status == ItemComanda.StatusPreparo.EM_PREPARO and usuario and not item.preparado_por_id:
            item.preparado_por = usuario
            campos.append('preparado_por')
        item.save(update_fields=campos)
        notificar = cls._NOTIFICAR_POR_STATUS.get(novo_status)
        if notificar:
            notificar(item)
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
        notificar_item_cancelado(item)
        return item
