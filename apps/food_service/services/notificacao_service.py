"""
Notificações automáticas do Food Service, em cima do sistema de
notificações já existente em `apps.core` (sino já visível em toda tela
autenticada, com polling ao vivo -- não precisa de UI nova aqui).

São por filial (todo mundo logado na filial vê), não por usuário -- é
assim que `Notificacao` já funciona hoje. `update_or_create` com
`referencia_tipo`/`referencia_id` evita duplicar a mesma notificação se o
evento disparar de novo (ex.: o poll de atraso rodando a cada 10s).
"""
from django.urls import reverse

from apps.core.models import Notificacao


def _comanda_url(comanda):
    return reverse('food_service:comanda-detail', kwargs={'pk': comanda.pk})


def _notificar_item(item, tipo, titulo, mensagem):
    return Notificacao.objects.update_or_create(
        filial=item.comanda.filial,
        tipo=tipo,
        referencia_tipo='item_comanda',
        referencia_id=f'{tipo}:{item.pk}',
        defaults={
            'titulo': titulo,
            'mensagem': mensagem,
            'url': _comanda_url(item.comanda),
            'ativa': True,
        },
    )[0]


def _descricao_item(item):
    mesas = ', '.join(str(m) for m in item.comanda.mesas.all()) or 'avulsa'
    return f'{item.quantidade}x {item.produto.descricao} — mesa {mesas}'


def notificar_pedido_recebido(item):
    return _notificar_item(
        item, Notificacao.Tipo.FOOD_PEDIDO_RECEBIDO,
        'Novo pedido na cozinha', _descricao_item(item),
    )


def notificar_pedido_iniciado(item):
    return _notificar_item(
        item, Notificacao.Tipo.FOOD_PEDIDO_INICIADO,
        'Pedido em preparo', _descricao_item(item),
    )


def notificar_pedido_pronto(item):
    return _notificar_item(
        item, Notificacao.Tipo.FOOD_PEDIDO_PRONTO,
        'Pedido pronto', _descricao_item(item),
    )


def notificar_pedido_entregue(item):
    return _notificar_item(
        item, Notificacao.Tipo.FOOD_PEDIDO_ENTREGUE,
        'Pedido entregue', _descricao_item(item),
    )


def notificar_item_cancelado(item):
    return _notificar_item(
        item, Notificacao.Tipo.FOOD_ITEM_CANCELADO,
        'Item cancelado na cozinha', _descricao_item(item),
    )


def notificar_item_atrasado(item):
    return _notificar_item(
        item, Notificacao.Tipo.FOOD_ITEM_ATRASADO,
        'Pedido atrasado na cozinha', _descricao_item(item),
    )


def notificar_produto_indisponivel(produto, filial):
    return Notificacao.objects.update_or_create(
        filial=filial,
        tipo=Notificacao.Tipo.FOOD_PRODUTO_INDISPONIVEL,
        referencia_tipo='produto',
        referencia_id=str(produto.pk),
        defaults={
            'titulo': 'Produto indisponível',
            'mensagem': f'{produto.descricao} foi marcado como indisponível na cozinha.',
            'url': reverse('food_service:kds'),
            'ativa': True,
        },
    )[0]
