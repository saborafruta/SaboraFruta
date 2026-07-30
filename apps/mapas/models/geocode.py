"""Cache de geocodificação e log de uso do provider."""
from django.db import models

from apps.core.models.base import TimestampedModel


class CacheGeocodificacao(TimestampedModel):
    """
    Endereço já geocodificado -> coordenada.

    Cache permanente e compartilhado por todas as empresas: um endereço é um
    fato do mundo, não da empresa. Isso corta drasticamente as chamadas ao
    provider (clientes distintos no mesmo prédio, recadastros, importações
    repetidas) — que é o recurso caro e limitado por política de uso.

    A chave é o hash do endereço normalizado (o mesmo que
    `CoordenadaMixin.hash_endereco_atual` produz).
    """

    endereco_hash = models.CharField(max_length=32, primary_key=True)
    endereco_consultado = models.CharField(max_length=300)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    precisao = models.CharField(max_length=12, blank=True)
    provider = models.CharField(max_length=30, blank=True)
    # Guarda também as falhas: evita reconsultar eternamente um endereço que
    # o provider não resolve. `tentativas` permite desistir depois de N.
    encontrado = models.BooleanField(default=True)
    tentativas = models.PositiveSmallIntegerField(default=1)
    erro = models.CharField(max_length=160, blank=True)

    class Meta:
        db_table = 'mapas_cache_geocodificacao'
        verbose_name = 'Cache de geocodificação'
        verbose_name_plural = 'Cache de geocodificação'
        indexes = [
            models.Index(fields=['encontrado', 'tentativas']),
        ]

    def __str__(self):
        return f'{self.endereco_consultado[:60]} -> {self.latitude},{self.longitude}'
