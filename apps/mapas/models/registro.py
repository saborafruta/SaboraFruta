"""
Registro do que o módulo de mapas faz (§14).

Sem isto, quatro indicadores do painel não teriam de onde sair: km em rota,
tempo em rota, economia da otimização e clientes sugeridos. Rotas e otimizações
eram calculadas, mostradas e descartadas — nada ficava.

O registro é **do que foi planejado**, não do que foi percorrido. Medir
percurso real depende do rastreamento (§13), que está em standby. Os rótulos do
painel dizem isso: um número de km apresentado como "percorrido" quando é
"calculado" levaria a conclusões erradas sobre a operação.
"""
from django.db import models

from apps.core.models.base import TimestampedModel


class RegistroRota(TimestampedModel):
    """
    Uma rota calculada no mapa (§4) ou otimizada (§5).

    Uma linha por cálculo. O usuário pode calcular a mesma rota várias vezes
    enquanto ajusta as paradas, e cada tentativa vira uma linha — por isso o
    painel soma por período e não trata isto como "entregas realizadas".
    """

    filial = models.ForeignKey(
        'core.Filial', on_delete=models.CASCADE, related_name='rotas_mapa',
    )
    usuario = models.ForeignKey(
        'core.Usuario', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    paradas = models.PositiveSmallIntegerField(default=0)
    distancia_m = models.PositiveIntegerField(default=0)
    duracao_s = models.PositiveIntegerField(default=0)

    # Preenchidos só quando houve otimização: a distância da ordem original,
    # para o painel poder mostrar o que se economizou.
    otimizada = models.BooleanField(default=False)
    distancia_antes_m = models.PositiveIntegerField(null=True, blank=True)
    duracao_antes_s = models.PositiveIntegerField(null=True, blank=True)

    provider = models.CharField(max_length=30, blank=True)

    class Meta:
        db_table = 'mapas_registro_rota'
        indexes = [
            models.Index(fields=['filial', '-created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f'Rota {self.paradas} paradas — {self.distancia_m / 1000:.1f} km'

    @property
    def economia_m(self):
        """Metros poupados pela otimização; 0 quando não houve ou não melhorou."""
        if not self.otimizada or self.distancia_antes_m is None:
            return 0
        return max(0, self.distancia_antes_m - self.distancia_m)


class SugestaoProximidade(TimestampedModel):
    """
    Uma consulta de clientes próximos a uma entrega (§8).

    Guarda quantos clientes foram oferecidos, não quantos viraram venda:
    ligar sugestão a pedido exigiria carregar a origem pelo PDV inteiro, o que
    é bem além do que o painel precisa. O indicador responde "quanta
    oportunidade o sistema colocou na mesa".
    """

    filial = models.ForeignKey(
        'core.Filial', on_delete=models.CASCADE, related_name='sugestoes_mapa',
    )
    venda_pdv_id = models.BigIntegerField(null=True, blank=True)
    raio_m = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'mapas_sugestao_proximidade'
        indexes = [
            models.Index(fields=['filial', '-created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.total} sugestões (raio {self.raio_m} m)'
