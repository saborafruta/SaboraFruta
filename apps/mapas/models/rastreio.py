"""
Rastreamento de motoristas (§13).

Duas tabelas com propósitos diferentes:

- `PosicaoMotorista` — **uma linha por motorista**, sobrescrita. É o que o mapa
  ao vivo lê. Uma consulta de "onde estão todos agora" que varresse o histórico
  ficaria mais lenta a cada dia de uso.
- `PontoPercurso` — o histórico, para desenhar por onde passou. Cresce, então é
  gravado com filtro (ver `RastreioService`) e tem expurgo por idade.

O §12 (cercas) continua não dependendo de nenhuma das duas: ele deduz o estado
dos próprios eventos. Rastreamento e cerca compartilham a entrada de posição,
mas não o armazenamento.
"""
from django.db import models

from apps.core.models.base import TimestampedModel


class PosicaoMotorista(TimestampedModel):
    """Última posição conhecida de um motorista."""

    motorista = models.OneToOneField(
        'cadastros.Motorista', on_delete=models.CASCADE, related_name='posicao',
    )
    filial = models.ForeignKey(
        'core.Filial', on_delete=models.CASCADE, related_name='posicoes_motorista',
    )
    latitude = models.FloatField()
    longitude = models.FloatField()
    momento = models.DateTimeField()

    # Vem do GPS quando o aparelho informa; senão é calculada entre duas
    # posições consecutivas. Nula quando não dá para saber — melhor um traço
    # na tela que um zero que parece "parado".
    velocidade_kmh = models.FloatField(null=True, blank=True)
    precisao_m = models.PositiveIntegerField(null=True, blank=True)

    # Para onde está indo. O Kanban de delivery guarda o entregador como texto
    # livre, então não há como deduzir o destino — quem informa é o próprio
    # motorista, na tela de rastreio.
    destino_venda = models.ForeignKey(
        'pdv.VendaPDV', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    destino_cliente = models.ForeignKey(
        'cadastros.Cliente', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )

    class Meta:
        db_table = 'mapas_posicao_motorista'
        verbose_name = 'Posição do motorista'
        verbose_name_plural = 'Posições dos motoristas'
        indexes = [
            models.Index(fields=['filial', '-momento']),
        ]

    def __str__(self):
        return f'{self.motorista_id} em {self.latitude},{self.longitude}'

    @property
    def destino(self):
        """Cliente de destino, venha ele da venda ou do vínculo direto."""
        if self.destino_cliente_id:
            return self.destino_cliente
        if self.destino_venda_id:
            return self.destino_venda.cliente
        return None


class PontoPercurso(TimestampedModel):
    """
    Um ponto do trajeto, para desenhar por onde o motorista passou.

    Só é gravado quando o motorista se moveu o bastante ou passou tempo
    suficiente — ver `RastreioService.DISTANCIA_MINIMA_M`. Guardar toda
    posição recebida encheria a tabela com centenas de pontos idênticos de um
    veículo parado no semáforo.
    """

    motorista = models.ForeignKey(
        'cadastros.Motorista', on_delete=models.CASCADE, related_name='percurso',
    )
    filial = models.ForeignKey(
        'core.Filial', on_delete=models.CASCADE, related_name='+',
    )
    latitude = models.FloatField()
    longitude = models.FloatField()
    momento = models.DateTimeField()
    velocidade_kmh = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = 'mapas_ponto_percurso'
        verbose_name = 'Ponto do percurso'
        verbose_name_plural = 'Pontos do percurso'
        ordering = ['momento']
        indexes = [
            models.Index(fields=['motorista', 'momento']),
            models.Index(fields=['filial', 'momento']),
        ]

    def __str__(self):
        return f'{self.motorista_id} @ {self.momento:%d/%m %H:%M}'
