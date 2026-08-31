"""Arquivo de caixa importado: nunca integra títulos, extratos ou saldos bancários."""
import uuid

from django.db import models
from apps.core.models.base import FilialManager, TimestampedModel


class LoteCaixaHistorico(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    filial = models.ForeignKey('core.Filial', on_delete=models.PROTECT)
    arquivo = models.CharField(max_length=255)
    arquivo_sha256 = models.CharField(max_length=64)
    usuario = models.ForeignKey('core.Usuario', on_delete=models.SET_NULL, null=True, blank=True)
    objects = FilialManager()

    class Meta:
        db_table = 'caixa_historico_lotes'


class DiaCaixaHistorico(TimestampedModel):
    filial = models.ForeignKey('core.Filial', on_delete=models.PROTECT)
    lote = models.ForeignKey(LoteCaixaHistorico, on_delete=models.PROTECT, related_name='dias')
    data = models.DateField()
    aba_origem = models.CharField(max_length=100)
    conteudo_sha256 = models.CharField(max_length=64)
    saldo_anterior_informado = models.DecimalField(max_digits=16, decimal_places=2)
    saldo_final_informado = models.DecimalField(max_digits=16, decimal_places=2)
    total_entradas = models.DecimalField(max_digits=16, decimal_places=2)
    total_saidas = models.DecimalField(max_digits=16, decimal_places=2)
    observacoes = models.JSONField(default=list, blank=True)
    objects = FilialManager()

    class Meta:
        db_table = 'caixa_historico_dias'
        ordering = ['data']
        constraints = [models.UniqueConstraint(fields=['filial', 'data'], name='caixa_hist_filial_data_uniq')]


class MovimentoCaixaHistorico(models.Model):
    class Tipo(models.TextChoices):
        ENTRADA = 'entrada', 'Entrada'
        SAIDA = 'saida', 'Saída'

    dia = models.ForeignKey(DiaCaixaHistorico, on_delete=models.CASCADE, related_name='movimentos')
    tipo = models.CharField(max_length=7, choices=Tipo.choices)
    descricao = models.TextField(blank=True)
    valor = models.DecimalField(max_digits=16, decimal_places=2)
    valor_original = models.CharField(max_length=100)
    celula_origem = models.CharField(max_length=16)
    ordem = models.PositiveIntegerField()

    class Meta:
        db_table = 'caixa_historico_movimentos'
        ordering = ['dia__data', 'ordem', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['dia', 'celula_origem'], name='caixa_hist_dia_celula_uniq'),
            models.CheckConstraint(condition=models.Q(valor__gt=0), name='caixa_hist_valor_positivo'),
            models.CheckConstraint(condition=models.Q(tipo__in=['entrada', 'saida']), name='caixa_hist_tipo_valido'),
        ]
