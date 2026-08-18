"""
Encaixe — o risco que define quanto tecido a peça gasta.

Aqui o aproveitamento é CALCULADO, não informado: área útil dividida pela
área utilizada. A área útil (a soma das áreas dos moldes) vem do CAD ou da
medição; a utilizada sai de comprimento × largura, que a própria tela
conhece. É a diferença em relação ao registro de corte, onde o
aproveitamento era digitado por não haver de onde tirá-lo — com o encaixe
cadastrado, o corte passa a ler daqui e ninguém mais digita esse número.

O encaixe é do PRODUTO, não do corte: o mesmo risco serve para todos os
enfestos daquele modelo naquela largura de tecido. Amarrá-lo ao corte
obrigaria a recadastrar o mesmo encaixe a cada enfesto, e o histórico de
aproveitamento por modelo — que é o que mostra se a modelagem melhorou —
não existiria.
"""
from decimal import Decimal

from django.db import models

from apps.core.models.base import ActiveModel, FilialManager, FilialScopedModel

CEM = Decimal('100')


class Encaixe(FilialScopedModel, ActiveModel):
    """Um risco: comprimento, largura, peças e o aproveitamento que dá."""

    nome = models.CharField(
        max_length=120,
        help_text='Ex.: Camisa gola redonda — P ao GG, tecido 1,60 m.',
    )

    produto = models.ForeignKey(
        'moda.ProdutoModa', on_delete=models.PROTECT, null=True, blank=True,
        related_name='encaixes',
        help_text='O produto que este risco atende. Em branco para um encaixe genérico.',
    )
    modelo = models.ForeignKey(
        'moda.Modelo', on_delete=models.PROTECT, null=True, blank=True,
        related_name='encaixes',
    )
    tecido = models.ForeignKey(
        'moda.Tecido', on_delete=models.PROTECT, null=True, blank=True,
        related_name='encaixes',
    )

    # ── Medidas ──────────────────────────────────────────────────────────
    comprimento = models.DecimalField(
        max_digits=8, decimal_places=3, default=Decimal('0'),
        verbose_name='Comprimento do encaixe (m)',
        help_text='O comprimento do risco, em metros.',
    )
    largura_tecido = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal('0'),
        verbose_name='Largura do tecido (m)',
        help_text='Largura útil do tecido, já descontada a ourela.',
    )
    quantidade_pecas = models.PositiveIntegerField(
        default=0, verbose_name='Quantidade de peças',
        help_text='Quantas peças cabem neste risco, somando todos os tamanhos.',
    )

    # A única entrada que o sistema não tem como deduzir: depende do molde.
    # Vem do CAD (Audaces, Optitex, Modaris) ou da medição do risco.
    area_util = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal('0'),
        verbose_name='Área útil (m²)',
        help_text='Soma da área dos moldes encaixados. Sai do CAD — é o que o sistema não consegue calcular sozinho.',
    )

    arquivo = models.FileField(
        upload_to='moda/encaixes/', blank=True, null=True,
        help_text='O arquivo do risco: PLT, DXF, PDF ou imagem.',
    )
    observacao = models.TextField(blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_encaixes'
        ordering = ['nome']
        unique_together = [('filial', 'nome')]
        indexes = [models.Index(fields=['filial', 'produto'])]
        verbose_name = 'Encaixe'
        verbose_name_plural = 'Encaixes'

    def __str__(self):
        return self.nome

    # ── Áreas ────────────────────────────────────────────────────────────

    @property
    def area_utilizada(self) -> Decimal:
        """Área do retângulo de tecido que o risco ocupa: comprimento × largura."""
        return (
            (self.comprimento or Decimal('0')) * (self.largura_tecido or Decimal('0'))
        ).quantize(Decimal('0.0001'))

    @property
    def aproveitamento(self) -> Decimal:
        """
        Área útil ÷ área utilizada, em porcento.

        Sem área útil informada devolve zero — zero aqui significa "ninguém
        mediu", e é diferente de um encaixe que realmente aproveita nada.
        Sem área utilizada também: dividir por zero num painel de produção
        seria trocar um número ausente por uma exceção.
        """
        utilizada = self.area_utilizada
        if not utilizada or not self.area_util:
            return Decimal('0')
        return (self.area_util / utilizada * CEM).quantize(Decimal('0.01'))

    @property
    def perda_percentual(self) -> Decimal:
        aproveitamento = self.aproveitamento
        if not aproveitamento:
            return Decimal('0')
        return (CEM - aproveitamento).quantize(Decimal('0.01'))

    @property
    def area_perdida(self) -> Decimal:
        """Metros quadrados de tecido que viram retalho neste risco."""
        return (self.area_utilizada - (self.area_util or Decimal('0'))).quantize(Decimal('0.0001'))

    @property
    def medido(self) -> bool:
        """Tem os três números necessários para o aproveitamento existir."""
        return bool(self.area_util and self.comprimento and self.largura_tecido)

    # ── Consumo ──────────────────────────────────────────────────────────

    @property
    def consumo(self) -> Decimal:
        """
        Metros lineares que o risco gasta por folha.

        É o próprio comprimento: uma folha de enfesto percorre o risco
        inteiro. Fica como propriedade em vez de campo para não haver dois
        números dizendo a mesma coisa e divergindo.
        """
        return (self.comprimento or Decimal('0')).quantize(Decimal('0.0001'))

    @property
    def consumo_por_peca(self) -> Decimal:
        """Metros lineares por peça — o número que vai para a ficha técnica."""
        if not self.quantidade_pecas:
            return Decimal('0')
        return (self.consumo / self.quantidade_pecas).quantize(Decimal('0.0001'))

    @property
    def area_por_peca(self) -> Decimal:
        if not self.quantidade_pecas:
            return Decimal('0')
        return ((self.area_util or Decimal('0')) / self.quantidade_pecas).quantize(Decimal('0.0001'))

    @property
    def alertas(self) -> list[str]:
        """O que impede este encaixe de servir para alguma coisa."""
        avisos = []
        if not self.comprimento or not self.largura_tecido:
            avisos.append('Sem comprimento ou largura: a área utilizada não pode ser calculada.')
        if not self.area_util:
            avisos.append(
                'Sem área útil: ela vem do CAD e é o único número que o sistema '
                'não deduz. Sem ela não há aproveitamento.'
            )
        if self.area_util and self.area_utilizada and self.area_util > self.area_utilizada:
            avisos.append(
                f'A área útil ({self.area_util} m²) é maior que a utilizada '
                f'({self.area_utilizada} m²) — os moldes não caberiam no risco. '
                f'Confira as medidas.'
            )
        if not self.quantidade_pecas:
            avisos.append('Sem quantidade de peças: o consumo por peça fica sem base.')
        return avisos
