"""Rota publicada para o celular do motoboy, protegida por URL-capacidade."""
import secrets
from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.core.models import Filial
from apps.core.models.base import TimestampedModel


def gerar_token_rota_delivery():
    # 128 bits: curto o bastante para compartilhar e impraticável de adivinhar.
    return secrets.token_urlsafe(16)


class RotaDeliveryPublica(TimestampedModel):
    """Um link fixo por filial apontando para a rota publicada mais recente."""

    filial = models.OneToOneField(
        Filial, on_delete=models.CASCADE, related_name='rota_delivery_publica',
    )
    token = models.CharField(
        max_length=32, unique=True, editable=False, default=gerar_token_rota_delivery,
    )
    pedido_ids = models.JSONField(default=list, blank=True)
    pedido_etas = models.JSONField(default=dict, blank=True)
    pedido_status_anteriores = models.JSONField(default=dict, blank=True)
    paradas_extras = models.JSONField(default=list, blank=True)
    ordem_paradas = models.JSONField(default=list, blank=True)
    paradas_extras_concluidas = models.JSONField(default=list, blank=True)
    combustivel_preco = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('0.00'),
    )
    autonomia_km_l = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('0.00'),
    )
    minutos_por_parada = models.PositiveSmallIntegerField(default=5)
    rfm_r5_dias = models.PositiveSmallIntegerField(default=30)
    rfm_r4_dias = models.PositiveSmallIntegerField(default=60)
    rfm_r3_dias = models.PositiveSmallIntegerField(default=90)
    rfm_r2_dias = models.PositiveSmallIntegerField(default=180)
    rfm_configuracao = models.JSONField(default=dict, blank=True)
    entregador = models.CharField(max_length=100, blank=True)
    ativa = models.BooleanField(default=True)

    class Meta:
        db_table = 'pdv_rotas_delivery_publicas'
        verbose_name = 'Rota pública de delivery'
        verbose_name_plural = 'Rotas públicas de delivery'

    def __str__(self):
        return f'Rota delivery — {self.filial}'


class RotaDelivery(TimestampedModel):
    """Rota operacional persistente, independente das configurações da filial."""

    class Status(models.TextChoices):
        RASCUNHO = 'rascunho', 'Rascunho'
        EM_ROTA = 'em_rota', 'Em rota'
        FINALIZADA = 'finalizada', 'Finalizada'

    filial = models.ForeignKey(
        Filial, on_delete=models.CASCADE, related_name='rotas_delivery',
    )
    nome = models.CharField(max_length=100, default='Nova rota')
    token = models.CharField(
        max_length=32, unique=True, editable=False, default=gerar_token_rota_delivery,
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RASCUNHO,
    )
    estado = models.JSONField(default=dict, blank=True)
    pedido_ids = models.JSONField(default=list, blank=True)
    pedido_etas = models.JSONField(default=dict, blank=True)
    pedido_status_anteriores = models.JSONField(default=dict, blank=True)
    paradas_extras = models.JSONField(default=list, blank=True)
    ordem_paradas = models.JSONField(default=list, blank=True)
    paradas_extras_concluidas = models.JSONField(default=list, blank=True)
    conferencia_itens = models.JSONField(default=dict, blank=True)
    entregador = models.CharField(max_length=100, blank=True)
    distancia_km = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    tempo_total_s = models.PositiveIntegerField(default=0)
    combustivel_litros = models.DecimalField(max_digits=10, decimal_places=3, default=Decimal('0.000'))
    custo_combustivel = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    publicada_em = models.DateTimeField(null=True, blank=True)
    finalizada_em = models.DateTimeField(null=True, blank=True)
    ativa = models.BooleanField(default=True)

    class Meta:
        db_table = 'pdv_rotas_delivery'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['filial', 'status'], name='pdv_rota_filial_status_idx'),
            models.Index(fields=['filial', 'finalizada_em'], name='pdv_rota_filial_fim_idx'),
        ]

    def finalizar(self):
        self.status = self.Status.FINALIZADA
        self.finalizada_em = timezone.now()
        self.ativa = False

    def __str__(self):
        return f'{self.nome} — {self.filial}'
