"""
Custos que a ficha do ERP não tem campo para guardar.

A `FichaTecnica` traz mão de obra e indireto, e a receita traz matéria-prima e
embalagem pelos itens. Sobra tudo o que uma fábrica de congelados paga e não
cabe em nenhum dos quatro: ENERGIA -- que aqui não é detalhe, porque câmara
fria e túnel rodam 24 horas -- água, frete da fruta, depreciação do túnel,
manutenção da despolpadeira.

LINHA CADASTRÁVEL, E NÃO UM CAMPO `custo_energia`. Um campo fixo resolveria
energia e deixaria os outros de fora; e em fábrica que rateia energia dentro do
indireto ele ficaria zerado para sempre, ocupando espaço na tela e ensinando a
ignorar aquele bloco. Linha cadastrável é a mesma mecânica para os dois casos.

A BASE MUDA A CONTA, e é por isso que ela existe:

  · POR BATIDA -- o custo é o mesmo para 100 kg ou 1.000 kg. Setup de linha,
    higienização, laudo do lote;
  · POR KG -- acompanha o peso. Energia do túnel é assim: congelar o dobro
    custa o dobro;
  · POR UNIDADE -- acompanha a contagem. Rótulo, etiqueta, selo.

Rateá-los todos por batida seria mais simples e diria a coisa errada: uma
batida pequena carregaria a energia de uma grande, e o custo por quilo do lote
pequeno sairia inflado sem que nada tivesse acontecido de verdade.
"""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel


class CustoReceita(FilialScopedModel):
    """Um custo adicional da receita — energia, água, frete, depreciação."""

    class Base(models.TextChoices):
        BATIDA = 'batida', 'Por batida'
        KG = 'kg', 'Por quilo produzido'
        UNIDADE = 'unidade', 'Por unidade produzida'

    receita = models.ForeignKey(
        'polpa.Receita', on_delete=models.CASCADE, related_name='custos_extras',
    )
    nome = models.CharField(max_length=60, help_text='Ex.: Energia do túnel')
    valor = models.DecimalField(
        max_digits=12, decimal_places=4,
        validators=[MinValueValidator(Decimal('0'))],
    )
    base = models.CharField(
        max_length=10, choices=Base.choices, default=Base.BATIDA,
    )
    observacao = models.CharField(max_length=160, blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_custos_receita'
        ordering = ['receita', 'nome']
        unique_together = [('receita', 'nome')]
        indexes = [models.Index(fields=['filial', 'ativo'])]
        verbose_name = 'Custo adicional da receita'
        verbose_name_plural = 'Custos adicionais da receita'

    def __str__(self):
        return f'{self.nome}: {self.valor} ({self.get_base_display()})'

    def total_para(self, quantidade, peso) -> Decimal:
        """
        Quanto este custo pesa numa batida deste tamanho.

        `quantidade` em unidades produzidas, `peso` em quilos. Base que não
        tem grandeza para multiplicar devolve ZERO em vez de cair na batida:
        um custo por quilo numa receita sem peso cadastrado não é "o valor
        cheio", é uma conta que não dá para fazer -- e assumi-la cheia
        inflaria o custo do lote sem que ninguém percebesse de onde veio.
        """
        zero = Decimal('0')
        valor = self.valor or zero
        if self.base == self.Base.BATIDA:
            return valor
        if self.base == self.Base.KG:
            return valor * (peso or zero)
        return valor * (quantidade or zero)
