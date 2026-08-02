"""
Cercas virtuais e os eventos de entrada/saída (§12).

Uma cerca só serve se alguém informar onde o motorista está. Isso é o §13
(rastreamento), que estava em standby — por isso veio junto o mínimo dele: um
endpoint de posição. Sem uma fonte de posição a cerca existe, mas nunca dispara.

**Não há tabela de pings.** O estado "está dentro" é deduzido do último evento
do par (motorista, cerca): se foi `entrada`, está dentro. Guardar cada posição
recebida geraria milhares de linhas por dia por motorista para responder uma
pergunta que dois registros respondem.
"""
from django.db import models

from apps.core.models.base import TimestampedModel


class Geofence(TimestampedModel):
    """
    Cerca circular: um ponto e um raio.

    Círculo e não polígono de propósito. O caso da especificação é "raio de
    300 metros", e o teste de dentro/fora vira uma conta de distância — que já
    existe e é barata. Regiões de formato livre já são atendidas pelos
    territórios (§11), que servem a outra pergunta: lá é "que clientes moram
    nesta região", aqui é "o veículo chegou".
    """

    filial = models.ForeignKey(
        'core.Filial', on_delete=models.CASCADE, related_name='geofences',
    )
    nome = models.CharField(max_length=120)
    latitude = models.FloatField()
    longitude = models.FloatField()
    raio_m = models.PositiveIntegerField(
        default=300, help_text='Raio da cerca em metros.',
    )
    ativo = models.BooleanField(default=True)

    # Âncora opcional: cerca criada em cima de um cadastro. Guardar o vínculo
    # permite saber depois "por que esta cerca existe" — e a coordenada é
    # copiada, não lida por FK, para a cerca não se mover sozinha quando
    # alguém corrige o endereço do cliente.
    cliente = models.ForeignKey(
        'cadastros.Cliente', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='geofences',
    )
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = 'mapas_geofence'
        verbose_name = 'Cerca virtual'
        verbose_name_plural = 'Cercas virtuais'
        ordering = ['nome']
        indexes = [
            models.Index(fields=['filial', 'ativo']),
        ]

    def __str__(self):
        return f'{self.nome} ({self.raio_m} m)'


class EventoGeofence(TimestampedModel):
    """Entrada ou saída de um motorista numa cerca."""

    class Tipo(models.TextChoices):
        ENTRADA = 'entrada', 'Entrada'
        SAIDA = 'saida', 'Saída'

    geofence = models.ForeignKey(
        Geofence, on_delete=models.CASCADE, related_name='eventos',
    )
    motorista = models.ForeignKey(
        'cadastros.Motorista', on_delete=models.CASCADE, related_name='eventos_geofence',
    )
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    momento = models.DateTimeField()
    latitude = models.FloatField()
    longitude = models.FloatField()
    # Distância ao centro no instante do evento: com ela dá para conferir
    # depois se o disparo foi na borda (GPS impreciso) ou bem dentro.
    distancia_m = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'mapas_evento_geofence'
        verbose_name = 'Evento de cerca'
        verbose_name_plural = 'Eventos de cerca'
        ordering = ['-momento']
        indexes = [
            models.Index(fields=['geofence', 'motorista', '-momento']),
            models.Index(fields=['-momento']),
        ]

    def __str__(self):
        return f'{self.motorista_id} {self.tipo} {self.geofence_id}'
