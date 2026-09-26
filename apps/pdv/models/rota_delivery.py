"""Rota publicada para o celular do motoboy, protegida por URL-capacidade."""
import secrets
from decimal import Decimal

from django.db import models

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
    entregador = models.CharField(max_length=100, blank=True)
    ativa = models.BooleanField(default=True)

    class Meta:
        db_table = 'pdv_rotas_delivery_publicas'
        verbose_name = 'Rota pública de delivery'
        verbose_name_plural = 'Rotas públicas de delivery'

    def __str__(self):
        return f'Rota delivery — {self.filial}'
