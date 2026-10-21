from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models.base import FilialScopedModel


class EnderecoEstoque(FilialScopedModel):
    """Posicao fisica reutilizavel dentro de um deposito."""

    deposito = models.ForeignKey(
        'estoque.Deposito', on_delete=models.PROTECT, related_name='enderecos',
    )
    codigo = models.CharField(
        max_length=80,
        help_text='Ex.: RUA-A / ESTANTE-03 / PRATELEIRA-02.',
    )
    descricao = models.CharField(max_length=160, blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'estoque_enderecos'
        ordering = ['deposito__nome', 'codigo', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['deposito', 'codigo'],
                name='endereco_codigo_unico_por_deposito',
            ),
        ]
        verbose_name = 'Endereco de estoque'
        verbose_name_plural = 'Enderecos de estoque'

    def clean(self):
        if self.deposito_id and self.filial_id != self.deposito.filial_id:
            raise ValidationError('O endereco deve pertencer a filial do deposito.')

    def save(self, *args, **kwargs):
        self.codigo = (self.codigo or '').strip().upper()
        if self.deposito_id:
            self.filial_id = self.deposito.filial_id
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.deposito.nome} - {self.codigo}'


class SaldoEndereco(models.Model):
    """Parcela do saldo produto x deposito atribuida a um endereco fisico."""

    estoque = models.ForeignKey(
        'estoque.Estoque', on_delete=models.CASCADE, related_name='saldos_enderecos',
    )
    endereco = models.ForeignKey(
        EnderecoEstoque, on_delete=models.PROTECT, related_name='saldos',
    )
    quantidade = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    prioridade_separacao = models.PositiveSmallIntegerField(
        default=100,
        help_text='Menor numero aparece primeiro para a separacao.',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'estoque_saldos_enderecos'
        ordering = ['prioridade_separacao', 'endereco__codigo', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['estoque', 'endereco'],
                name='saldo_unico_por_estoque_endereco',
            ),
            models.CheckConstraint(
                condition=models.Q(quantidade__gte=0),
                name='saldo_endereco_quantidade_nao_negativa',
            ),
        ]
        verbose_name = 'Saldo por endereco'
        verbose_name_plural = 'Saldos por endereco'

    def clean(self):
        if not self.estoque_id or not self.endereco_id:
            return
        if self.estoque.deposito_id != self.endereco.deposito_id:
            raise ValidationError('O endereco deve pertencer ao deposito deste saldo.')
        if self.estoque.filial_id != self.endereco.filial_id:
            raise ValidationError('O endereco deve pertencer a filial deste saldo.')
        if Decimal(self.quantidade or 0) < 0:
            raise ValidationError('A quantidade enderecada nao pode ser negativa.')

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.estoque.produto} - {self.endereco}: {self.quantidade}'
