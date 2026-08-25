"""
O endereço dentro da câmara, e a temperatura que ela registrou.

DE "RUA 3" PARA UM ENDEREÇO DE VERDADE. A seção 15 guardava a localização
num campo de texto, e texto livre resolve enquanto a fábrica tem uma câmara
pequena: "Rua 3", "rua 3", "R3" e "rua três" são a mesma prateleira para a
pessoa e quatro endereços diferentes para o sistema. Na hora de perguntar "o
que está na rua 3?", a resposta vem quebrada em quatro.

O ENDEREÇO ESTRUTURADO É QUE PERMITE AS PERGUNTAS QUE IMPORTAM:

  · o que está nesta posição? (inventário de uma prateleira, sem varrer a
    câmara inteira de porta aberta);
  · esta posição está livre? (para quem chega com o pallet e precisa decidir
    onde colocar sem procurar);
  · a câmara passou da capacidade? (a soma só fecha se as posições tiverem
    capacidade declarada).

O TEXTO LIVRE NÃO SUMIU. `LoteArmazenado.endereco` continua valendo para
quem não mapeou a câmara — obrigar o cadastro de cada prateleira antes de
poder guardar o primeiro lote seria trocar um problema real (não saber onde
está) por um pior (não registrar nada).

A TEMPERATURA ATUAL É A ÚLTIMA LEITURA, e não um campo que alguém edita.
Campo editável guarda o número que a pessoa digitou por último e não diz
QUANDO — e "está a -18°C" sem data é uma afirmação sobre o passado que se
lê como presente.
"""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel

ZERO = Decimal('0')


class Posicao(FilialScopedModel):
    """Um endereço dentro da câmara: corredor, rua, prateleira e posição."""

    camara = models.ForeignKey(
        'polpa.Camara', on_delete=models.CASCADE, related_name='posicoes',
    )

    # OS QUATRO NÍVEIS SÃO TEXTO, e não número. Câmara real tem "Corredor A",
    # "Rua 3", "Prateleira B2" — forçar inteiro obrigaria a fábrica a
    # renomear o que já está pintado no chão.
    corredor = models.CharField(max_length=20, blank=True)
    rua = models.CharField(max_length=20, blank=True)
    prateleira = models.CharField(max_length=20, blank=True)
    posicao = models.CharField(max_length=20, blank=True)

    capacidade_kg = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text='Quanto cabe aqui. Nulo é "ninguém mediu".',
    )
    ativo = models.BooleanField(default=True)
    observacao = models.CharField(max_length=160, blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_posicoes'
        ordering = ['camara__nome', 'corredor', 'rua', 'prateleira', 'posicao']
        unique_together = [
            ('camara', 'corredor', 'rua', 'prateleira', 'posicao'),
        ]
        indexes = [models.Index(fields=['filial', 'camara', 'ativo'])]
        verbose_name = 'Posição'
        verbose_name_plural = 'Posições'

    def __str__(self):
        return f'{self.camara.nome} · {self.codigo}' if self.codigo else self.camara.nome

    @property
    def codigo(self) -> str:
        """
        O endereço em texto, só com os níveis que existem.

        Câmara que só tem rua não vira "—-3—-": os níveis vazios somem, e o
        que sobra é o que a pessoa fala em voz alta.
        """
        partes = [p for p in (self.corredor, self.rua, self.prateleira, self.posicao) if p]
        return '-'.join(partes)

    @property
    def peso_ocupado(self) -> Decimal:
        """Quanto está guardado aqui, pelos lotes que apontam para cá."""
        total = ZERO
        for armazenado in self.lotes.select_related('lote', 'lote__produto'):
            total += armazenado.peso or ZERO
        return total

    @property
    def livre(self) -> bool:
        return not self.lotes.exists()

    @property
    def ocupacao(self) -> Decimal | None:
        if not self.capacidade_kg:
            return None
        return (self.peso_ocupado / self.capacidade_kg * 100).quantize(Decimal('0.1'))


class LeituraTemperatura(FilialScopedModel):
    """Uma medição de temperatura de uma câmara, com hora e responsável."""

    camara = models.ForeignKey(
        'polpa.Camara', on_delete=models.CASCADE, related_name='leituras',
    )
    temperatura = models.DecimalField(max_digits=6, decimal_places=2)
    medida_em = models.DateTimeField(db_index=True)
    medido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='leituras_polpa',
    )
    observacao = models.CharField(max_length=160, blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_leituras_temperatura'
        ordering = ['-medida_em']
        indexes = [
            models.Index(fields=['filial', 'camara', '-medida_em']),
        ]
        verbose_name = 'Leitura de temperatura'
        verbose_name_plural = 'Leituras de temperatura'

    def __str__(self):
        return f'{self.camara.nome}: {self.temperatura}°C'

    @property
    def desvio(self) -> Decimal | None:
        """
        Quantos graus fora da faixa. `None` quando está dentro — ou quando a
        câmara não tem faixa, que é diferente de estar tudo bem.
        """
        camara = self.camara
        if camara.temperatura_max is not None and self.temperatura > camara.temperatura_max:
            return (self.temperatura - camara.temperatura_max).quantize(Decimal('0.01'))
        if camara.temperatura_min is not None and self.temperatura < camara.temperatura_min:
            return (self.temperatura - camara.temperatura_min).quantize(Decimal('0.01'))
        return None

    @property
    def fora_da_faixa(self) -> bool:
        return self.desvio is not None
