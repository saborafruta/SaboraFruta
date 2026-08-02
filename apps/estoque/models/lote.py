"""Lote de produto — rastreabilidade completa de origem e validade."""
from django.db import models

from apps.core.models.base import FilialScopedModel


class LoteProduto(FilialScopedModel):
    """
    Lote identificado de um produto. Rastreia:
    - Origem (fornecedor + nota fiscal de entrada) ou ordem de produção
    - Fabricação e validade
    - Quantidades inicial e atual (baixa conforme consumo)
    - Custo específico deste lote (pode divergir do custo médio do produto)
    """

    class Status(models.TextChoices):
        ATIVO = 'ativo', 'Ativo'
        ESGOTADO = 'esgotado', 'Esgotado'
        VENCIDO = 'vencido', 'Vencido'
        BLOQUEADO = 'bloqueado', 'Bloqueado'
        QUARENTENA = 'quarentena', 'Quarentena'

    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.PROTECT, related_name='lotes',
    )
    numero_lote = models.CharField(max_length=60)
    data_fabricacao = models.DateField(null=True, blank=True)
    data_validade = models.DateField(
        null=True, blank=True, db_index=True,
        help_text='NULL = sem validade',
    )
    fornecedor = models.ForeignKey(
        'cadastros.Fornecedor', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='lotes',
        help_text='Se o lote veio de compra externa',
    )
    numero_nota_entrada = models.CharField(max_length=20, blank=True)
    ordem_producao_id = models.BigIntegerField(
        null=True, blank=True,
        help_text='Se o lote foi gerado internamente por uma OP',
    )

    quantidade_inicial = models.DecimalField(
        max_digits=12, decimal_places=3, default=0,
    )
    quantidade_atual = models.DecimalField(
        max_digits=12, decimal_places=3, default=0, db_index=True,
    )
    custo_unitario = models.DecimalField(
        max_digits=14, decimal_places=4, default=0,
        help_text='Custo específico deste lote',
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ATIVO, db_index=True,
    )
    motivo_bloqueio = models.TextField(blank=True)

    class Meta:
        db_table = 'lotes_produto'
        ordering = ['data_validade', 'created_at']
        unique_together = [('produto', 'numero_lote', 'filial')]
        indexes = [
            models.Index(fields=['filial', 'status']),
            models.Index(fields=['produto', 'status', 'data_validade']),
            models.Index(fields=['data_validade']),
        ]
        verbose_name = 'Lote de Produto'
        verbose_name_plural = 'Lotes de Produto'

    def __str__(self):
        return f'{self.produto} — lote {self.numero_lote}'

    @property
    def esta_vencido(self):
        from django.utils import timezone
        if not self.data_validade:
            return False
        return self.data_validade < timezone.localdate()

    @property
    def dias_para_vencer(self):
        from django.utils import timezone
        if not self.data_validade:
            return None
        delta = self.data_validade - timezone.localdate()
        return delta.days

    @property
    def dias_vencido(self):
        dias = self.dias_para_vencer
        if dias is None or dias >= 0:
            return 0
        return abs(dias)

    def disponivel_para_venda(self) -> bool:
        """Lote só é vendável se ATIVO e não-vencido e com quantidade > 0."""
        if self.status != self.Status.ATIVO:
            return False
        if self.quantidade_atual <= 0:
            return False
        if self.esta_vencido:
            return False
        return True
