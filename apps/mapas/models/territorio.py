"""Atribuição de clientes a territórios (praças com polígono)."""
from django.db import models

from apps.core.models.base import TimestampedModel


class ClienteTerritorio(TimestampedModel):
    """
    Cliente que cai dentro do polígono de uma praça.

    É uma tabela materializada, e não uma consulta ao vivo: sem PostGIS o
    ponto-em-polígono roda em Python, então descobrir "os clientes desta
    região" a cada request custaria O(clientes × vértices). Aqui o custo é
    pago uma vez no recálculo e a leitura vira um JOIN indexado.

    Fica em `mapas` (e não uma FK em Cliente) para manter a direção da
    dependência: mapas conhece cadastros, cadastros não conhece mapas.

    Polígonos podem se sobrepor, então um cliente pode aparecer em mais de
    um território — a unicidade é do par, não do cliente.
    """

    praca = models.ForeignKey(
        'cadastros.Praca', on_delete=models.CASCADE, related_name='clientes_territorio',
    )
    cliente = models.ForeignKey(
        'cadastros.Cliente', on_delete=models.CASCADE, related_name='territorios',
    )

    class Meta:
        db_table = 'mapas_cliente_territorio'
        verbose_name = 'Cliente por território'
        verbose_name_plural = 'Clientes por território'
        constraints = [
            models.UniqueConstraint(
                fields=['praca', 'cliente'], name='uniq_cliente_por_praca',
            ),
        ]
        indexes = [
            models.Index(fields=['praca']),
            models.Index(fields=['cliente']),
        ]

    def __str__(self):
        return f'{self.cliente_id} em {self.praca_id}'
