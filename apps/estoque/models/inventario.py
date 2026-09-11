"""Inventário (contagem física) e seus itens."""
from django.db import models

from apps.core.models.base import FilialScopedModel, TimestampedModel


class Inventario(FilialScopedModel):
    """Inventário de estoque — pode travar movimentações durante a contagem."""

    class Status(models.TextChoices):
        ABERTO = 'aberto', 'Aberto'
        EM_CONTAGEM = 'em_contagem', 'Em Contagem'
        EM_CONFERENCIA = 'em_conferencia', 'Em Conferência'
        FECHADO = 'fechado', 'Fechado'
        CANCELADO = 'cancelado', 'Cancelado'

    descricao = models.CharField(max_length=100, blank=True)
    deposito = models.ForeignKey(
        'estoque.Deposito', on_delete=models.PROTECT, related_name='inventarios',
        help_text='Depósito contado neste inventário.',
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ABERTO, db_index=True,
    )
    bloquear_movimentacoes = models.BooleanField(
        default=False, help_text='Bloqueia movimentações de estoque durante a contagem',
    )
    data_inicio = models.DateTimeField()
    data_fim = models.DateTimeField(null=True, blank=True)
    usuario_inicio = models.ForeignKey(
        'core.Usuario', on_delete=models.PROTECT,
        related_name='inventarios_abertos',
    )
    usuario_fechamento = models.ForeignKey(
        'core.Usuario', on_delete=models.PROTECT, null=True, blank=True,
        related_name='inventarios_fechados',
    )
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = 'inventarios'
        ordering = ['-data_inicio']
        verbose_name = 'Inventário'
        verbose_name_plural = 'Inventários'

    def save(self, *args, **kwargs):
        # Rede de segurança: quem abre um inventário sem indicar depósito
        # cai no padrão da filial — o comportamento de antes desta dimensão.
        if self.deposito_id is None and self.filial_id is not None:
            from apps.estoque.models.deposito import Deposito
            self.deposito_id = Deposito.padrao_id(self.filial_id)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'Inventário {self.data_inicio:%d/%m/%Y} - {self.get_status_display()}'


class ItemInventario(TimestampedModel):
    """Item contado durante o inventário com diferença calculada."""

    inventario = models.ForeignKey(
        Inventario, on_delete=models.CASCADE, related_name='itens',
    )
    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.PROTECT, related_name='+',
    )
    lote = models.ForeignKey(
        'estoque.LoteProduto', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    quantidade_sistema = models.DecimalField(
        max_digits=12, decimal_places=3,
        help_text='Estoque no sistema no momento do inventário',
    )
    quantidade_contada = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
    )
    diferenca = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        help_text='contada - sistema (positiva = sobra; negativa = falta)',
    )
    valor_unitario = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True,
    )
    valor_diferenca = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
    )
    justificativa = models.TextField(blank=True)
    usuario_contagem = models.ForeignKey(
        'core.Usuario', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    data_contagem = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'itens_inventario'
        ordering = ['inventario', 'produto']

    def calcular_diferenca(self):
        if self.quantidade_contada is not None:
            self.diferenca = self.quantidade_contada - self.quantidade_sistema
            if self.valor_unitario:
                self.valor_diferenca = self.diferenca * self.valor_unitario
