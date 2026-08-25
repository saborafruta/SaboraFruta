"""
A matéria-prima separada para uma ordem — quem está com o quê.

`Estoque.quantidade_reservada` guarda o TOTAL reservado de um produto e não
diz para quem. Sem esta tabela, "há 300 kg de manga reservados" é verdade e
inútil: ninguém sabe se são de uma OP ou de seis, nem o que liberar quando
uma delas é cancelada.

QUEM MEXE NO SALDO É O `MovimentacaoService`, como em todo o resto do ERP.
Aqui só se registra a quem a separação pertence.

O PORQUÊ DISTO NUMA FÁBRICA DE POLPA. Fruta é perecível e disputada: a mesma
câmara atende a batida de polpa, a de açaí e a de sorvete no mesmo dia. Até
agora nada segurava material para uma OP em andamento — as três "tinham" a
mesma manga, e a última a chegar na balança descobria que não tinha. E como
a baixa só acontece no ENCERRAMENTO da ordem, a janela em que o saldo mente é
a batida inteira, não um instante.

A RESERVA MORRE QUANDO O CONSUMO NASCE. `OrdemProducaoService.encerrar` baixa
o insumo de verdade; se a reserva continuasse de pé, o mesmo material estaria
reservado E consumido, e o disponível ficaria negativo sem nada errado ter
acontecido. Liberar na conclusão não é limpeza, é a outra metade da conta.
"""
from django.conf import settings
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel


class ReservaInsumo(FilialScopedModel):
    """Insumo separado para uma ordem de polpa."""

    class Status(models.TextChoices):
        ATIVA = 'ativa', 'Ativa'
        CONSUMIDA = 'consumida', 'Consumida'
        CANCELADA = 'cancelada', 'Cancelada'

    ordem = models.ForeignKey(
        'polpa.OrdemPolpa', on_delete=models.CASCADE, related_name='reservas',
    )
    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.PROTECT,
        related_name='reservas_polpa',
    )
    quantidade = models.DecimalField(max_digits=12, decimal_places=4)

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.ATIVA,
        db_index=True,
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reservas_polpa',
    )
    observacao = models.CharField(max_length=160, blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_reservas_insumo'
        ordering = ['ordem', 'produto']
        indexes = [
            models.Index(fields=['filial', 'status']),
            models.Index(fields=['ordem', 'status']),
        ]
        verbose_name = 'Insumo reservado'
        verbose_name_plural = 'Insumos reservados'

    def __str__(self):
        return f'{self.quantidade} de {self.produto}'

    @property
    def ativa(self) -> bool:
        return self.status == self.Status.ATIVA
