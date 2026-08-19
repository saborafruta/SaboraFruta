"""
Controle de corte — o que foi cortado, com quanto de tecido.

Uma ordem pode ter vários cortes: cores diferentes, lotes de tecido
diferentes, ou o mesmo produto cortado em dois dias. Por isso o registro é
por CORTE e não por ordem — amarrar um por OP obrigaria a somar na mão o
que saiu de cada enfesto, e o aproveitamento de cada encaixe se perderia na
média.

Produto e modelo são lidos da ordem. Tecido e cor podem ser sobrescritos
aqui, porque o corte é feito com um rolo específico: a mesma OP com três
cores tem três cortes, cada um com o seu.

O APROVEITAMENTO VEM DO ENCAIXE quando há um cadastrado: lá ele é
calculado (área útil ÷ área utilizada) e não depende de ninguém copiar o
número certo para cá. Sem encaixe, sobra o campo digitado neste registro,
que é como funcionava antes de o cadastro de encaixe existir.

Não confundir com a comparação do bloco de baixo: aproveitamento é quanto do
tecido virou peça no risco; planejado × utilizado é a diferença entre o que
a ficha previa e o que o enfesto gastou. São perguntas diferentes e ficam em
lugares diferentes da tela.
"""
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel

from .qr import ComCodigoQr

CEM = Decimal('100')


class RegistroCorte(ComCodigoQr, FilialScopedModel):
    """Um enfesto/corte de uma ordem de produção."""

    PREFIXO_QR = 'LT'

    class Status(models.TextChoices):
        PLANEJADO = 'planejado', 'Planejado'
        CORTADO = 'cortado', 'Cortado'
        CANCELADO = 'cancelado', 'Cancelado'

    numero = models.PositiveIntegerField(db_index=True)

    ordem = models.ForeignKey(
        'moda.OrdemProducao', on_delete=models.PROTECT, related_name='cortes',
    )

    # Em branco, herdam do item da ordem. Não copiados na gravação: se
    # alguém corrigir o tecido do pedido, o corte que não pediu exceção
    # acompanha.
    tecido = models.ForeignKey(
        'moda.Tecido', on_delete=models.PROTECT, null=True, blank=True,
        related_name='cortes', help_text='Em branco, usa o tecido do item da ordem.',
    )
    cor = models.ForeignKey(
        'moda.Cor', on_delete=models.PROTECT, null=True, blank=True,
        related_name='cortes', help_text='Em branco, usa a cor do item da ordem.',
    )
    lote = models.CharField(
        max_length=40, blank=True,
        help_text='Lote do rolo de tecido. É por ele que se rastreia um defeito de matéria-prima.',
    )

    data = models.DateField(null=True, blank=True)
    responsavel = models.CharField(max_length=80, blank=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PLANEJADO, db_index=True,
    )

    # Sincronizada com a grade quando há grade — ver `services/corte.py`.
    quantidade = models.PositiveIntegerField(
        default=0, help_text='Peças cortadas neste enfesto.',
    )

    # ── Encaixe ──────────────────────────────────────────────────────────
    # Com encaixe cadastrado, o aproveitamento vem CALCULADO de lá (área
    # útil ÷ área utilizada) e o campo abaixo deixa de valer. Sem encaixe,
    # sobra o valor digitado — que é o que existia antes desta ligação.
    encaixe = models.ForeignKey(
        'moda.Encaixe', on_delete=models.PROTECT, null=True, blank=True,
        related_name='cortes',
        help_text='Ligando o encaixe, o aproveitamento passa a ser calculado dele.',
    )
    largura_tecido = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal('0'),
        verbose_name='Largura do tecido (m)',
        validators=[MinValueValidator(Decimal('0'))],
    )
    comprimento_encaixe = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('0'),
        verbose_name='Comprimento do encaixe (m)',
        validators=[MinValueValidator(Decimal('0'))],
    )
    folhas = models.PositiveIntegerField(
        default=1, help_text='Folhas do enfesto — quantas camadas de tecido foram sobrepostas.',
    )
    aproveitamento = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0'),
        verbose_name='Aproveitamento (%)',
        help_text='Vem do encaixe: quanto da área do tecido virou peça. Ex.: 87,5.',
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(CEM)],
    )

    # ── Consumo ──────────────────────────────────────────────────────────
    # Nulo = calcula da ficha técnica. Nulo e não zero: zero é um plano
    # legítimo (peça que não usa este tecido) e precisa ser distinguível de
    # "não informei, use a ficha".
    consumo_planejado = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
        verbose_name='Consumo planejado (m)',
        help_text='Em branco, sai da ficha técnica: consumo do tecido principal × quantidade.',
        validators=[MinValueValidator(Decimal('0'))],
    )
    consumo_real = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal('0'),
        verbose_name='Consumo real (m)',
        help_text='Metros de tecido efetivamente gastos.',
        validators=[MinValueValidator(Decimal('0'))],
    )

    observacao = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_cortes'
        ordering = ['-data', '-numero']
        unique_together = [('filial', 'numero')]
        indexes = [
            models.Index(fields=['filial', 'status']),
            models.Index(fields=['ordem']),
        ]
        verbose_name = 'Registro de corte'
        verbose_name_plural = 'Registros de corte'

    def __str__(self):
        return f'Corte #{self.numero:04d} — {self.ordem.numero}'

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = self._proximo_numero()
        super().save(*args, **kwargs)

    def _proximo_numero(self) -> int:
        ultimo = (
            RegistroCorte.all_objects
            .filter(filial_id=self.filial_id)
            .aggregate(models.Max('numero'))['numero__max']
        )
        return (ultimo or 0) + 1

    # ── Leituras da ordem ────────────────────────────────────────────────

    @property
    def item(self):
        return self.ordem.item

    @property
    def produto(self):
        return self.item.produto

    @property
    def descricao_produto(self) -> str:
        return self.item.nome_exibicao

    @property
    def modelo(self):
        return self.item.modelo

    @property
    def tecido_efetivo(self):
        if self.tecido_id:
            return self.tecido
        return self.item.tecido or (self.produto.tecido if self.produto else None)

    @property
    def cor_efetiva(self):
        return self.cor if self.cor_id else self.item.cor

    # ── Encaixe ──────────────────────────────────────────────────────────

    @property
    def aproveitamento_efetivo(self) -> Decimal:
        """
        O aproveitamento que vale: o do encaixe, se houver; senão o digitado.

        O do encaixe vem primeiro porque é calculado da área e não depende
        de alguém lembrar de copiar o número certo para cá.
        """
        if self.encaixe_id and self.encaixe.medido:
            return self.encaixe.aproveitamento
        return self.aproveitamento or Decimal('0')

    @property
    def aproveitamento_do_encaixe(self) -> bool:
        """Para a tela dizer de onde veio o número."""
        return bool(self.encaixe_id and self.encaixe.medido)

    @property
    def perda_percentual(self) -> Decimal:
        """
        O que sobra do aproveitamento. Aproveitamento 87,5% = perda 12,5%.

        Sem aproveitamento medido devolve zero, e não 100: zero ali
        significa "ninguém mediu ainda", e mostrar 100% de perda numa ficha
        recém-aberta seria alarme falso todo dia.
        """
        aproveitamento = self.aproveitamento_efetivo
        if not aproveitamento:
            return Decimal('0')
        return (CEM - aproveitamento).quantize(Decimal('0.01'))

    @property
    def consumo_do_encaixe(self) -> Decimal:
        """Metros que o enfesto gasta: comprimento × folhas."""
        return (
            (self.comprimento_encaixe or Decimal('0')) * self.folhas
        ).quantize(Decimal('0.0001'))

    @property
    def pecas_por_folha(self) -> Decimal:
        if not self.folhas:
            return Decimal('0')
        return (Decimal(self.quantidade) / self.folhas).quantize(Decimal('0.01'))

    @property
    def perda_metros(self) -> Decimal:
        """Metros de tecido que não viraram peça, pelo aproveitamento."""
        return (
            (self.consumo_real or Decimal('0')) * self.perda_percentual / CEM
        ).quantize(Decimal('0.0001'))

    # ── Consumo planejado × real ─────────────────────────────────────────

    @property
    def planejado(self) -> Decimal:
        """Consumo planejado, resolvido: o informado ou o da ficha técnica."""
        if self.consumo_planejado is not None:
            return self.consumo_planejado
        return self.planejado_da_ficha

    @property
    def planejado_da_ficha(self) -> Decimal:
        """
        Consumo do tecido principal na ficha × quantidade deste corte.

        Só o tecido principal: forro, linha e aviamentos não saem do rolo
        que está sendo cortado, e somá-los inflaria o planejado contra o
        qual o consumo real é comparado.
        """
        produto = self.produto
        ficha = getattr(produto, 'ficha', None) if produto else None
        if ficha is None:
            return Decimal('0')

        from .ficha import MaterialFicha

        principais = [
            m for m in ficha.materiais.all()
            if m.tipo == MaterialFicha.Tipo.TECIDO_PRINCIPAL
        ]
        por_peca = sum((m.consumo_bruto for m in principais), Decimal('0'))
        return (por_peca * self.quantidade).quantize(Decimal('0.0001'))

    @property
    def variacao(self) -> Decimal:
        """Real − planejado. Positivo = gastou mais do que devia."""
        return ((self.consumo_real or Decimal('0')) - self.planejado).quantize(Decimal('0.0001'))

    @property
    def variacao_percentual(self) -> Decimal:
        planejado = self.planejado
        if not planejado:
            return Decimal('0')
        return (self.variacao / planejado * CEM).quantize(Decimal('0.1'))

    @property
    def estourou(self) -> bool:
        """Gastou mais tecido do que o planejado."""
        return self.variacao > 0

    @property
    def consumo_por_peca(self) -> Decimal:
        if not self.quantidade:
            return Decimal('0')
        return ((self.consumo_real or Decimal('0')) / self.quantidade).quantize(Decimal('0.0001'))

    # ── Grade ────────────────────────────────────────────────────────────

    @property
    def total_da_grade(self) -> int:
        return sum(g.quantidade for g in self.grade.all())

    @property
    def grade_bate(self) -> bool:
        """
        Grade e quantidade conferem.

        A regra é a mesma do pedido: nunca permitir divergência entre a
        grade e o total. Aqui vale mais ainda, porque a grade do corte é o
        que a costura vai receber.
        """
        return not self.grade.exists() or self.total_da_grade == self.quantidade


class ItemCorte(models.Model):
    """Quantas peças de cada tamanho saíram deste corte."""

    corte = models.ForeignKey(
        RegistroCorte, on_delete=models.CASCADE, related_name='grade',
    )
    tamanho = models.ForeignKey(
        'moda.Tamanho', on_delete=models.PROTECT, related_name='cortes',
    )
    quantidade = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'moda_cortes_grade'
        ordering = ['tamanho__ordem', 'tamanho__sigla']
        unique_together = [('corte', 'tamanho')]
        verbose_name = 'Tamanho do corte'
        verbose_name_plural = 'Grade do corte'

    def __str__(self):
        return f'{self.tamanho.sigla}: {self.quantidade}'
