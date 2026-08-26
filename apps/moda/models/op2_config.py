from django.db import models

from apps.core.models.base import ActiveModel, FilialScopedModel


class OpcaoEstruturaOP2(FilialScopedModel, ActiveModel):
    """Opção editável da estrutura usada na sobreposição da OP 2.0."""

    tipo_peca = models.CharField(max_length=40, db_index=True)
    tipo_label = models.CharField(max_length=80)
    campo = models.CharField(max_length=80, db_index=True)
    valor = models.CharField(max_length=120)
    ordem = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'moda_op2_estrutura_opcoes'
        ordering = ['tipo_label', 'campo', 'ordem', 'valor']
        unique_together = [('filial', 'tipo_peca', 'campo', 'valor')]
        indexes = [
            models.Index(fields=['filial', 'tipo_peca', 'campo', 'ativo']),
        ]
        verbose_name = 'Opção de estrutura da OP 2.0'
        verbose_name_plural = 'Opções de estrutura da OP 2.0'

    def __str__(self):
        return f'{self.tipo_label} · {self.campo}: {self.valor}'
