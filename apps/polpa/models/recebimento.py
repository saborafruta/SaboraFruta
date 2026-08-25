"""
O recebimento da fruta — onde a cadeia começa e onde o dinheiro se decide.

TRÊS COISAS ACONTECEM NA BALANÇA, e um ERP genérico registra só a primeira:

  1. QUANTO ENTROU. Peso bruto menos tara. Parece trivial, mas é o número
     que o produtor recebe — e uma tara chutada é o erro que ninguém
     confere depois, porque o caminhão já foi embora.

  2. O QUE ENTROU. Brix, pH, impureza, fruta danificada. É aqui que se
     aceita ou recusa, e é a ÚNICA hora em que recusar ainda é possível:
     depois de descarregada, a fruta é problema da fábrica.

  3. QUANTO SE PAGA POR ISSO. Carga com 8% de impureza não vale o peso da
     balança. O desconto é registrado no recebimento, não combinado por
     telefone, senão a nota do produtor e o custo do lote divergem e o
     custo real do produto nunca fecha.

O REGISTRO É IMUTÁVEL DEPOIS DE APROVADO. Peso e classificação param de
aceitar edição quando o lote nasce: mudar o peso de origem depois que a
fruta já foi processada reescreveria o custo de um produto que já foi
vendido. Errou, cancela com motivo e refaz -- que deixa rastro.

O LOTE É DO ESTOQUE, não daqui. Aprovar cria um `estoque.LoteProduto`, que
é o mesmo lote que o resto do ERP já sabe ler. Um "lote de polpa" próprio
daria dois estoques da mesma fruta e nenhum dos dois confiável.
"""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel

ZERO = Decimal('0')


class Recebimento(FilialScopedModel):
    """Uma carga de fruta chegando: o romaneio da balança."""

    class Status(models.TextChoices):
        # A ordem É o processo: pesa, classifica, decide.
        PESAGEM = 'pesagem', 'Em pesagem'
        CLASSIFICACAO = 'classificacao', 'Aguardando classificação'
        APROVADO = 'aprovado', 'Aprovado'
        RECUSADO = 'recusado', 'Recusado'
        CANCELADO = 'cancelado', 'Cancelado'

    ABERTOS = (Status.PESAGEM, Status.CLASSIFICACAO)
    ENCERRADOS = (Status.APROVADO, Status.RECUSADO, Status.CANCELADO)

    numero = models.PositiveIntegerField(db_index=True)

    fruta = models.ForeignKey(
        'polpa.Fruta', on_delete=models.PROTECT, related_name='recebimentos',
    )
    produtor = models.ForeignKey(
        'cadastros.Fornecedor', on_delete=models.PROTECT,
        related_name='recebimentos_polpa',
    )

    data = models.DateField(db_index=True)
    hora_chegada = models.TimeField(null=True, blank=True)

    # ── O caminhão ───────────────────────────────────────────────────────
    placa = models.CharField(max_length=10, blank=True)
    motorista = models.CharField(max_length=80, blank=True)
    nota_fiscal = models.CharField(max_length=20, blank=True)

    # ── A balança ────────────────────────────────────────────────────────
    peso_bruto = models.DecimalField(
        max_digits=12, decimal_places=3, default=ZERO,
        validators=[MinValueValidator(0)], help_text='kg, com o veículo.',
    )
    tara = models.DecimalField(
        max_digits=12, decimal_places=3, default=ZERO,
        validators=[MinValueValidator(0)], help_text='kg do veículo e das caixas.',
    )
    # DESCONTO SEPARADO DO PESO, e não abatido do líquido: quem confere
    # precisa ver o peso que a balança marcou E o que foi descontado. Um
    # número só esconde a negociação dentro da pesagem.
    desconto_kg = models.DecimalField(
        max_digits=12, decimal_places=3, default=ZERO,
        validators=[MinValueValidator(0)],
        help_text='kg descontados por impureza, fruta podre ou excesso de água.',
    )

    # ── A classificação ──────────────────────────────────────────────────
    # Tudo nulo enquanto ninguém mediu. Zero seria uma medição que aconteceu
    # e deu zero -- e Brix zero reprovaria a carga sozinho.
    temperatura_chegada = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='°C da fruta na chegada.',
    )
    brix = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    ph = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    acidez = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True,
        help_text='% de ácido cítrico.',
    )
    impureza = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)], help_text='% de folha, galho e terra.',
    )
    danificada = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)], help_text='% de fruta batida ou podre.',
    )
    classificado_em = models.DateTimeField(null=True, blank=True)
    classificado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='classificacoes_polpa',
    )

    # ── O dinheiro ───────────────────────────────────────────────────────
    preco_kg = models.DecimalField(
        max_digits=12, decimal_places=4, default=ZERO,
        validators=[MinValueValidator(0)], help_text='R$ por kg líquido pago ao produtor.',
    )

    # ── A decisão ────────────────────────────────────────────────────────
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.PESAGEM, db_index=True,
    )
    motivo_recusa = models.TextField(blank=True)
    decidido_em = models.DateTimeField(null=True, blank=True)
    decidido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='decisoes_recebimento_polpa',
    )

    lote = models.ForeignKey(
        'estoque.LoteProduto', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='recebimentos_polpa',
        help_text='Lote de matéria-prima criado quando a carga foi aprovada.',
    )

    observacao = models.TextField(blank=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='recebimentos_polpa',
    )

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_recebimentos'
        ordering = ['-data', '-numero']
        unique_together = [('filial', 'numero')]
        indexes = [
            models.Index(fields=['filial', 'status']),
            models.Index(fields=['filial', 'data']),
            models.Index(fields=['fruta', 'data']),
        ]
        verbose_name = 'Recebimento de fruta'
        verbose_name_plural = 'Recebimentos de fruta'

    def __str__(self):
        return f'Recebimento #{self.numero:05d} — {self.fruta}'

    def save(self, *args, **kwargs):
        if not self.numero:
            ultimo = (
                Recebimento.all_objects
                .filter(filial_id=self.filial_id)
                .aggregate(models.Max('numero'))['numero__max']
            )
            self.numero = (ultimo or 0) + 1
        super().save(*args, **kwargs)

    # ── Pesos ────────────────────────────────────────────────────────────

    @property
    def peso_liquido(self) -> Decimal:
        """O que a balança diz que entrou. Nunca negativo."""
        return max(self.peso_bruto - self.tara, ZERO)

    @property
    def peso_aceito(self) -> Decimal:
        """O que se paga: líquido menos o desconto da classificação."""
        return max(self.peso_liquido - self.desconto_kg, ZERO)

    @property
    def valor_total(self) -> Decimal:
        return (self.peso_aceito * self.preco_kg).quantize(Decimal('0.01'))

    @property
    def percentual_desconto(self) -> Decimal:
        if self.peso_liquido <= ZERO:
            return ZERO
        return (self.desconto_kg / self.peso_liquido * 100).quantize(Decimal('0.01'))

    @property
    def rendimento_previsto(self) -> Decimal:
        """Quanto de polpa esta carga deveria dar, pela régua da fruta."""
        esperado = self.fruta.rendimento_esperado
        if not esperado:
            return ZERO
        return (self.peso_aceito * esperado / 100).quantize(Decimal('0.001'))

    # ── Situação ─────────────────────────────────────────────────────────

    @property
    def classificado(self) -> bool:
        return self.classificado_em is not None

    @property
    def encerrado(self) -> bool:
        return self.status in self.ENCERRADOS

    @property
    def editavel(self) -> bool:
        """
        Peso e classificação só mudam ANTES da decisão.

        Depois de aprovado a fruta já entrou no estoque e pode já estar
        processada: reescrever o peso de origem mudaria o custo de um
        produto que talvez já tenha sido vendido.
        """
        return not self.encerrado

    def reprovacoes(self) -> list[str]:
        """
        Onde esta carga fere a régua da fruta — em frases, não em códigos.

        DEVOLVE LISTA E NÃO TRAVA. Quem decide é a pessoa na balança: uma
        manga com Brix meio ponto abaixo pode ser aceita para um produto
        que leva açúcar, e travar isso faria a fábrica registrar outro
        número para conseguir seguir. O que o sistema garante é que a
        decisão fica ESCRITA junto do desvio.
        """
        fruta = self.fruta
        problemas = []

        if fruta.brix_minimo and self.brix is not None and self.brix < fruta.brix_minimo:
            problemas.append(
                f'Brix {self.brix} abaixo do mínimo {fruta.brix_minimo} — fruta verde.'
            )
        if fruta.ph_maximo and self.ph is not None and self.ph > fruta.ph_maximo:
            problemas.append(
                f'pH {self.ph} acima do máximo {fruta.ph_maximo} — acidez baixa demais.'
            )
        if (
            fruta.impureza_maxima and self.impureza is not None
            and self.impureza > fruta.impureza_maxima
        ):
            problemas.append(
                f'Impureza {self.impureza}% acima do limite {fruta.impureza_maxima}%.'
            )
        return problemas

    def pendencias(self) -> list[str]:
        """O que falta para poder decidir sobre a carga."""
        faltando = []
        if self.peso_liquido <= ZERO:
            faltando.append('Peso líquido zerado — pese o veículo cheio e vazio.')
        if not self.classificado:
            faltando.append('Classificação não registrada.')
        if self.preco_kg <= ZERO:
            faltando.append('Preço por kg não informado — sem ele não há custo do lote.')
        return faltando
