"""Controle de separação de filial em empresa independente."""
from django.conf import settings
from django.db import models

from .base import TimestampedModel


class SeparacaoFilial(TimestampedModel):
    """Processo auditavel para transformar uma filial em uma nova empresa."""

    class Status(models.TextChoices):
        RASCUNHO = 'rascunho', 'Rascunho'
        SIMULADO = 'simulado', 'Simulado'
        BLOQUEADO = 'bloqueado', 'Bloqueado'
        EXECUTANDO = 'executando', 'Executando'
        CONCLUIDO = 'concluido', 'Concluído'
        ERRO = 'erro', 'Erro'

    filial_origem = models.ForeignKey(
        'core.Filial',
        on_delete=models.PROTECT,
        related_name='separacoes',
    )
    empresa_origem = models.ForeignKey(
        'core.Empresa',
        on_delete=models.PROTECT,
        related_name='separacoes_origem',
    )
    empresa_destino = models.ForeignKey(
        'core.Empresa',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='separacoes_destino',
    )
    banco_destino = models.ForeignKey(
        'core.EmpresaBanco',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='separacoes_filial',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RASCUNHO,
        db_index=True,
    )
    plano_json = models.JSONField(default=dict, blank=True)
    simulacao_json = models.JSONField(default=dict, blank=True)
    backup_path = models.CharField(max_length=500, blank=True)
    backup_exportacao_path = models.CharField(max_length=500, blank=True)
    etapa_atual = models.CharField(max_length=120, blank=True)
    progresso_percentual = models.PositiveSmallIntegerField(default=0)
    executor_backend = models.CharField(max_length=30, blank=True)
    task_id = models.CharField(max_length=255, blank=True)
    despachado_em = models.DateTimeField(null=True, blank=True)
    relatorio_json = models.JSONField(default=dict, blank=True)
    rollback_json = models.JSONField(default=dict, blank=True)
    ultimo_erro = models.TextField(blank=True)
    iniciado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='separacoes_iniciadas',
    )
    confirmado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='separacoes_confirmadas',
    )
    concluido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'separacoes_filiais'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['filial_origem', 'status']),
            models.Index(fields=['empresa_origem', '-created_at']),
        ]
        verbose_name = 'Separação de filial'
        verbose_name_plural = 'Separações de filiais'

    def __str__(self):
        return f'Separacao {self.filial_origem_id} - {self.status}'
