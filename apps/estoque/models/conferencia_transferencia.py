from django.db import models

from apps.core.models.base import TimestampedModel


class ConferenciaTransferencia(TimestampedModel):
    class Status(models.TextChoices):
        AGUARDANDO = 'aguardando', 'Aguardando conferencia'
        EM_CONFERENCIA = 'em_conferencia', 'Em conferencia'
        CONFERIDA = 'conferida', 'Conferida'
        COM_DIVERGENCIA = 'com_divergencia', 'Conferida com divergencia'
        CANCELADA = 'cancelada', 'Cancelada'

    documento_numero = models.CharField(max_length=20, unique=True)
    filial_origem = models.ForeignKey(
        'core.Filial',
        on_delete=models.PROTECT,
        related_name='transferencias_enviadas_conferencia',
    )
    filial_destino = models.ForeignKey(
        'core.Filial',
        on_delete=models.PROTECT,
        related_name='transferencias_recebidas_conferencia',
    )
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.AGUARDANDO,
        db_index=True,
    )
    observacao_origem = models.TextField(blank=True)
    observacao_conferencia = models.TextField(blank=True)
    criada_por = models.ForeignKey(
        'core.Usuario',
        on_delete=models.PROTECT,
        related_name='transferencias_criadas_conferencia',
    )
    conferida_por = models.ForeignKey(
        'core.Usuario',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transferencias_conferidas',
    )
    conferida_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'transferencias_conferencias'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['filial_destino', 'status', '-created_at']),
            models.Index(fields=['filial_origem', '-created_at']),
        ]

    def __str__(self):
        return f'{self.documento_numero} - {self.get_status_display()}'


class ItemConferenciaTransferencia(TimestampedModel):
    class Ocorrencia(models.TextChoices):
        OK = 'ok', 'Recebido corretamente'
        FALTANTE = 'faltante', 'Quantidade faltante'
        TROCADO = 'trocado', 'Item trocado'

    conferencia = models.ForeignKey(
        ConferenciaTransferencia,
        on_delete=models.CASCADE,
        related_name='itens',
    )
    movimento_saida = models.OneToOneField(
        'estoque.MovimentacaoEstoque',
        on_delete=models.PROTECT,
        related_name='item_conferencia_transferencia',
    )
    produto_enviado = models.ForeignKey(
        'produtos.Produto',
        on_delete=models.PROTECT,
        related_name='itens_transferencia_enviados',
    )
    lote_enviado = models.ForeignKey(
        'estoque.LoteProduto',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='+',
    )
    quantidade_enviada = models.DecimalField(max_digits=12, decimal_places=3)
    quantidade_recebida = models.DecimalField(max_digits=12, decimal_places=3)
    ocorrencia = models.CharField(
        max_length=16,
        choices=Ocorrencia.choices,
        default=Ocorrencia.OK,
    )
    produto_recebido = models.ForeignKey(
        'produtos.Produto',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='itens_transferencia_recebidos',
    )
    quantidade_produto_recebido = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
    )
    observacao = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = 'transferencias_conferencias_itens'
        ordering = ['pk']

