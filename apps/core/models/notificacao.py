from django.conf import settings
from django.db import models

from apps.core.models.base import TimestampedModel


class Notificacao(TimestampedModel):
    class Tipo(models.TextChoices):
        TRANSFERENCIA_RECEBIDA = 'transferencia_recebida', 'Transferencia recebida'
        TRANSFERENCIA_CONFERIDA = 'transferencia_conferida', 'Transferencia conferida'
        ALERTA_SISTEMA = 'alerta_sistema', 'Alerta do sistema'
        FOOD_PEDIDO_RECEBIDO = 'food_pedido_recebido', 'Pedido recebido na cozinha'
        FOOD_PEDIDO_INICIADO = 'food_pedido_iniciado', 'Pedido em preparo'
        FOOD_PEDIDO_PRONTO = 'food_pedido_pronto', 'Pedido pronto'
        FOOD_PEDIDO_ENTREGUE = 'food_pedido_entregue', 'Pedido entregue'
        FOOD_ITEM_CANCELADO = 'food_item_cancelado', 'Item cancelado na cozinha'
        FOOD_ITEM_ATRASADO = 'food_item_atrasado', 'Item atrasado na cozinha'
        FOOD_PRODUTO_INDISPONIVEL = 'food_produto_indisponivel', 'Produto indisponível'

        # Alertas automáticos do vertical Moda. São CONDIÇÕES, não eventos:
        # a mesma referência é reaproveitada enquanto a condição vale, e
        # desativada quando ela passa. Ver `apps.moda.services.alertas`.
        MODA_PEDIDO_ATRASADO = 'moda_pedido_atrasado', 'Moda: pedido atrasado'
        MODA_PEDIDO_VENCENDO = 'moda_pedido_vencendo', 'Moda: pedido perto do prazo'
        MODA_MATERIAL_INSUFICIENTE = 'moda_material_insuficiente', 'Moda: material insuficiente'
        MODA_PRODUCAO_ABAIXO = 'moda_producao_abaixo', 'Moda: produção abaixo do planejado'
        MODA_SETOR_SOBRECARREGADO = 'moda_setor_sobrecarregado', 'Moda: setor sobrecarregado'
        MODA_REJEICAO_ALTA = 'moda_rejeicao_alta', 'Moda: alto índice de rejeição'
        MODA_APROVEITAMENTO_BAIXO = 'moda_aproveitamento_baixo', 'Moda: baixo aproveitamento de corte'

    filial = models.ForeignKey(
        'core.Filial',
        on_delete=models.CASCADE,
        related_name='notificacoes',
    )
    tipo = models.CharField(max_length=40, choices=Tipo.choices, db_index=True)
    titulo = models.CharField(max_length=140)
    mensagem = models.CharField(max_length=500, blank=True)
    url = models.CharField(max_length=500, blank=True)
    referencia_tipo = models.CharField(max_length=50, blank=True)
    referencia_id = models.CharField(max_length=80, blank=True)
    ativa = models.BooleanField(default=True, db_index=True)
    lida_por = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='NotificacaoLeitura',
        related_name='notificacoes_lidas',
        blank=True,
    )

    class Meta:
        db_table = 'notificacoes'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['filial', 'ativa', '-created_at']),
            models.Index(fields=['referencia_tipo', 'referencia_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['filial', 'tipo', 'referencia_tipo', 'referencia_id'],
                name='notificacao_referencia_unica',
                condition=~models.Q(referencia_id=''),
            ),
        ]

    def __str__(self):
        return self.titulo


class NotificacaoLeitura(models.Model):
    notificacao = models.ForeignKey(
        Notificacao,
        on_delete=models.CASCADE,
        related_name='leituras',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='leituras_notificacoes',
    )
    lida_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notificacoes_leituras'
        constraints = [
            models.UniqueConstraint(
                fields=['notificacao', 'usuario'],
                name='notificacao_leitura_usuario_unica',
            ),
        ]
