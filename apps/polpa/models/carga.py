"""
O que o romaneio de carga não sabia: a que temperatura o baú saiu.

POR QUE UMA FICHA, E NÃO UM ROMANEIO NOVO. O ERP já tem o documento da
carga (`logistica.RomaneioCarga`): motorista, placa, transportadora,
paradas, peso e status. Um "carregamento" próprio deste vertical daria dois
documentos para o mesmo caminhão, e a entrega ficaria com dois donos.

O QUE FALTAVA É UM DADO SÓ, e ele não cabe no romaneio porque não vale para
todo mundo: quem entrega parafuso não mede o baú. Numa fábrica de
congelados, essa medição é a última prova da cadeia de frio — depois dela o
produto some de vista, e a próxima temperatura conhecida é a do cliente,
que já não pode ser corrigida.

MEDIÇÃO TEM DONO E HORA. Um campo "temperatura" solto guarda o número
digitado por último e não diz quem mediu nem quando; a fiscalização
pergunta as três coisas juntas. Por isso `medida_em` e `medido_por` andam
colados ao valor.

A HORA DA SAÍDA É REGISTRADA, e não deduzida do status: o romaneio muda
para "em rota" e não guarda quando isso aconteceu, e "saiu 11h40" é o que
explica uma entrega que chegou morna às 15h.
"""
from django.conf import settings
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel


class CargaFria(FilialScopedModel):
    """A medição do baú de um romaneio — a última prova da cadeia de frio."""

    romaneio = models.OneToOneField(
        'logistica.RomaneioCarga', on_delete=models.CASCADE,
        related_name='ficha_polpa',
    )

    # NULO É "NINGUÉM MEDIU", e a tela diz isso. Zero seria um baú a 0°C,
    # que é uma afirmação — e a pior possível, porque parece plausível.
    temperatura_bau = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text='°C medidos dentro do baú, na hora de fechar a porta.',
    )
    medida_em = models.DateTimeField(null=True, blank=True)
    medido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='cargas_medidas_polpa',
    )

    saida_em = models.DateTimeField(
        null=True, blank=True,
        help_text='Quando o caminhão saiu de fato.',
    )
    observacao = models.TextField(blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_cargas_frias'
        ordering = ['-saida_em', '-id']
        verbose_name = 'Carga fria'
        verbose_name_plural = 'Cargas frias'

    def __str__(self):
        return f'{self.romaneio} — baú {self.temperatura_bau or "sem medição"}'

    @property
    def medida(self) -> bool:
        return self.temperatura_bau is not None

    @property
    def despachada(self) -> bool:
        return self.saida_em is not None
