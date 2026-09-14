"""Registro histórico de sugestões do equilíbrio -- sustenta indicadores e relatórios que precisam de série temporal (o motor em si nunca escreve isso sozinho; é gerado por rotina explícita)."""
from django.db import models

from apps.core.models.base import TimestampedModel


class SugestaoEqualizacaoSnapshot(TimestampedModel):
    """
    Uma linha por sugestão de transferência no momento em que o snapshot
    foi tirado (`created_at` é o "calculado em"). Gerado pela rotina
    diária de automação (ou manualmente) -- nunca pelo cálculo ao vivo
    que a tela de equilíbrio usa, pra não transformar uma leitura em
    escrita a cada carregamento de página.
    """

    empresa = models.ForeignKey(
        'core.Empresa', on_delete=models.CASCADE, related_name='snapshots_equalizacao',
    )
    produto = models.ForeignKey('produtos.Produto', on_delete=models.CASCADE, related_name='+')
    filial_origem = models.ForeignKey('core.Filial', on_delete=models.CASCADE, related_name='+')
    filial_destino = models.ForeignKey('core.Filial', on_delete=models.CASCADE, related_name='+')
    quantidade_sugerida = models.DecimalField(max_digits=12, decimal_places=3)
    score = models.PositiveSmallIntegerField()
    destino_meta = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        db_table = 'estoque_snapshots_equalizacao'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['empresa', 'created_at']),
            models.Index(fields=['produto', 'filial_origem', 'filial_destino', 'created_at']),
        ]
        verbose_name = 'Snapshot de sugestão de equalização'
        verbose_name_plural = 'Snapshots de sugestão de equalização'

    def __str__(self):
        return f'{self.produto} {self.filial_origem}->{self.filial_destino} @ {self.created_at:%d/%m %H:%M}'
