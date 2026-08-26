"""
O canhoto da entrega: quem recebeu, quando e a que temperatura.

O QUE O ROMANEIO JÁ SABIA E O QUE NÃO. `ItemRomaneioCarga` tem a parada e o
status ("entregue", "não entregue"), e é dele que a logística vive. O que
ele não tem é a PROVA: um status "entregue" sem nome, sem hora e sem
temperatura é a fábrica dizendo que entregou -- e, quando o cliente reclama
duas semanas depois, não existe nada a colocar do outro lado da mesa.

QUEM RECEBEU É NOME, não "sim". A pessoa que assina o canhoto é a que
responde por ter recebido o produto naquele estado. Guardar só um booleano
transforma toda divergência em palavra contra palavra.

A TEMPERATURA DA ENTREGA FECHA A CADEIA. O túnel prova o congelamento, a
câmara prova o armazenamento, o baú prova a saída -- e sem esta última
medição a corrente termina no portão da fábrica, que é justamente o trecho
que o cliente contesta.

NÃO ENTREGUE PRECISA DE MOTIVO. "Não entregue" sem razão vira número em
relatório e some; com o motivo, vira roteiro que muda -- cliente fechado às
duas da tarde, endereço errado no cadastro, recusa por temperatura.
"""
from django.conf import settings
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel


class EntregaFria(FilialScopedModel):
    """A prova de uma parada: quem recebeu, quando e em que temperatura."""

    class Ocorrencia(models.TextChoices):
        RECUSA = 'recusa', 'Recusada pelo cliente'
        TEMPERATURA = 'temperatura', 'Recusada por temperatura'
        AVARIA = 'avaria', 'Produto avariado'
        AUSENTE = 'ausente', 'Ninguém para receber'
        ENDERECO = 'endereco', 'Endereço não encontrado'
        OUTRA = 'outra', 'Outra'

    parada = models.OneToOneField(
        'logistica.ItemRomaneioCarga', on_delete=models.CASCADE,
        related_name='ficha_polpa',
    )

    entregue_em = models.DateTimeField(null=True, blank=True)
    recebido_por = models.CharField(
        max_length=120, blank=True,
        help_text='Nome de quem recebeu — é ele que responde pelo estado do produto.',
    )
    documento = models.CharField(max_length=30, blank=True)

    # NULO É "NINGUÉM MEDIU". Zero seria um produto a 0°C na porta do
    # cliente — uma afirmação, e das piores, porque parece plausível.
    temperatura = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text='°C medidos na entrega, antes de o produto sair do baú.',
    )

    ocorrencia = models.CharField(
        max_length=15, choices=Ocorrencia.choices, blank=True,
        help_text='Preenchido quando a parada não foi entregue.',
    )
    observacao = models.TextField(blank=True)

    registrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='entregas_polpa',
    )

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_entregas_frias'
        ordering = ['-entregue_em', '-id']
        verbose_name = 'Entrega'
        verbose_name_plural = 'Entregas'

    def __str__(self):
        return f'{self.parada.cliente_nome} — {self.recebido_por or "sem canhoto"}'

    @property
    def entregue(self) -> bool:
        return self.entregue_em is not None

    @property
    def medida(self) -> bool:
        return self.temperatura is not None
