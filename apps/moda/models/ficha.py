"""
Ficha técnica — a especificação de engenharia da peça.

O que a ficha **não** faz é tão importante quanto o que faz: ela não repete
modelo, coleção, tecido, grade nem referência. Esses campos já são do
`ProdutoModa`, e copiá-los aqui criaria duas verdades que divergem no dia em
que alguém corrigir uma e esquecer a outra. A tela mostra tudo isso lendo do
produto; a ficha guarda o que é dela: a lista de materiais, o consumo de
cada um e o custo que sai daí.

O custo também não é campo gravado. É somado dos materiais na leitura — um
total gravado ficaria velho no instante em que alguém mudasse o preço de um
aviamento, e ninguém confia num custo que pode estar velho.
"""
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel

from .qr import ComCodigoQr


class FichaTecnica(ComCodigoQr, FilialScopedModel):
    """A ficha de um produto. Uma por produto, versionada."""

    PREFIXO_QR = 'FT'

    class Status(models.TextChoices):
        RASCUNHO = 'rascunho', 'Rascunho'
        APROVADA = 'aprovada', 'Aprovada'
        OBSOLETA = 'obsoleta', 'Obsoleta'

    # OneToOne: a ficha É do produto. Permitir várias faria a tela de custo
    # ter de escolher uma, e não existe critério para essa escolha.
    produto = models.OneToOneField(
        'moda.ProdutoModa', on_delete=models.CASCADE, related_name='ficha',
    )

    versao = models.PositiveSmallIntegerField(
        default=1,
        help_text='Suba a versão quando mudar consumo ou material — o custo muda junto.',
    )
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.RASCUNHO, db_index=True,
    )

    descricao = models.TextField(
        blank=True,
        help_text='Especificação técnica: modelagem, costura, acabamentos, tolerâncias.',
    )

    # Desenho próprio da ficha. Em branco, a tela cai no desenho do produto:
    # a maioria das fichas usa o mesmo, e obrigar a reenviar seria trabalho
    # repetido para nada.
    desenho_tecnico = models.FileField(
        upload_to='moda/fichas/desenhos/', blank=True, null=True,
        help_text='PNG, JPG, PDF, CDR ou AI. Em branco, usa o desenho do produto.',
    )

    observacoes = models.TextField(blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_fichas_tecnicas'
        ordering = ['produto__codigo']
        indexes = [models.Index(fields=['filial', 'status'])]
        verbose_name = 'Ficha técnica'
        verbose_name_plural = 'Fichas técnicas'

    def __str__(self):
        return f'Ficha {self.produto.codigo} v{self.versao}'

    # ── Custo ────────────────────────────────────────────────────────────

    @property
    def custo_estimado(self) -> Decimal:
        """Soma do custo de todos os materiais, já com a perda de cada um."""
        return sum(
            (m.custo_total for m in self.materiais.all()), Decimal('0'),
        ).quantize(Decimal('0.01'))

    @property
    def custo_por_tipo(self) -> list[dict]:
        """
        Custo agrupado por tipo de material, do maior para o menor.

        Ordenado por valor e não pela ordem do formulário porque a pergunta
        que se faz olhando isto é "onde está indo o dinheiro" — e a resposta
        tem de estar na primeira linha.
        """
        totais: dict[str, Decimal] = {}
        for material in self.materiais.all():
            totais[material.tipo] = totais.get(material.tipo, Decimal('0')) + material.custo_total

        rotulos = dict(MaterialFicha.Tipo.choices)
        total_geral = sum(totais.values(), Decimal('0'))
        linhas = [
            {
                'tipo': tipo,
                'label': rotulos.get(tipo, tipo),
                'custo': valor.quantize(Decimal('0.01')),
                'percentual': (
                    (valor / total_geral * 100).quantize(Decimal('0.1'))
                    if total_geral else Decimal('0')
                ),
            }
            for tipo, valor in totais.items()
        ]
        return sorted(linhas, key=lambda x: x['custo'], reverse=True)

    @property
    def custo_com_mao_de_obra(self) -> Decimal:
        """
        Materiais + mão de obra do roteiro do produto.

        Sem roteiro, devolve só os materiais: a ficha não tem como inventar
        um custo de produção que ninguém informou, e um total inflado por
        chute seria pior do que um total incompleto e assumido como tal.
        """
        roteiro = getattr(self.produto, 'roteiro', None)
        if roteiro is None:
            return self.custo_estimado
        return (self.custo_estimado + roteiro.custo_total).quantize(Decimal('0.01'))

    # ── Peso por tamanho ─────────────────────────────────────────────────

    @property
    def pesos_por_grade(self) -> list[dict]:
        """
        O peso de cada tamanho, agrupado por grade — Adulto, Oversized, Baby
        Look, o que a peça for cortada.

        O peso não é um número só: a mesma camisa pesa 145 g no P e 230 g no
        XG, e uma peça pode ser cortada em mais de uma grade (a versão solta
        e a oversized do mesmo modelo). Um campo único não tinha como dizer
        nenhuma das duas coisas.
        """
        grupos: dict[int, dict] = {}
        for linha in self.pesos_tamanho.select_related('grade', 'tamanho'):
            grupo = grupos.setdefault(linha.grade_id, {
                'grade_id': linha.grade_id,
                'grade_nome': linha.grade.nome,
                'linhas': [],
            })
            grupo['linhas'].append(linha)
        return sorted(grupos.values(), key=lambda g: g['grade_nome'])

    @property
    def desenho(self):
        """O desenho da ficha; sem ele, o do produto."""
        return self.desenho_tecnico or self.produto.desenho_tecnico


class ImagemFicha(models.Model):
    """
    Foto ou desenho anexo à ficha.

    Modelo próprio, e não um punhado de `ImageField` na ficha, porque não dá
    para saber quantas imagens uma peça precisa: um conjunto com bordado tem
    frente, costas, detalhe da gola e detalhe do bordado. Campos fixos ou
    sobrariam ou faltariam.
    """

    ficha = models.ForeignKey(
        FichaTecnica, on_delete=models.CASCADE, related_name='imagens',
    )
    imagem = models.ImageField(upload_to='moda/fichas/imagens/')
    legenda = models.CharField(max_length=120, blank=True)
    ordem = models.PositiveIntegerField(default=0)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'moda_fichas_imagens'
        ordering = ['ordem', 'id']
        verbose_name = 'Imagem da ficha'
        verbose_name_plural = 'Imagens da ficha'

    def __str__(self):
        return self.legenda or f'Imagem {self.pk}'


class MaterialFicha(models.Model):
    """Um material da ficha, com consumo, perda e custo."""

    class Tipo(models.TextChoices):
        # A ordem é a da ficha de papel, não alfabética: quem preenche vai
        # de cima para baixo, começando pelo tecido.
        TECIDO_PRINCIPAL = 'tecido_principal', 'Tecido principal'
        FORRO = 'forro', 'Forro'
        LINHA = 'linha', 'Linha'
        ELASTICO = 'elastico', 'Elástico'
        ZIPER = 'ziper', 'Zíper'
        BOTAO = 'botao', 'Botão'
        ETIQUETA = 'etiqueta', 'Etiqueta'
        TAG = 'tag', 'Tag'
        EMBALAGEM = 'embalagem', 'Embalagem'
        AVIAMENTO = 'aviamento', 'Aviamentos'

    class Unidade(models.TextChoices):
        METRO = 'm', 'm'
        METRO2 = 'm2', 'm²'
        CENTIMETRO = 'cm', 'cm'
        QUILO = 'kg', 'kg'
        GRAMA = 'g', 'g'
        UNIDADE = 'un', 'un'
        PAR = 'par', 'par'
        PECA = 'pc', 'pç'
        ROLO = 'rolo', 'rolo'
        CONE = 'cone', 'cone'

    ficha = models.ForeignKey(
        FichaTecnica, on_delete=models.CASCADE, related_name='materiais',
    )

    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    descricao = models.CharField(
        max_length=140,
        help_text='Ex.: Malha Dry Fit 100% poliéster 140g, Zíper nylon nº 5 destacável.',
    )
    codigo = models.CharField(
        max_length=40, blank=True,
        help_text='Código do material no estoque ou no fornecedor.',
    )

    # A ponte com o estoque real. Sem ela o material é só texto e não há de
    # onde ler "estoque atual" -- a necessidade continua sendo calculada, mas
    # a tela não consegue dizer se falta.
    produto_estoque = models.ForeignKey(
        'produtos.Produto', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='materiais_moda',
        verbose_name='Produto no estoque',
        help_text='Ligue para o sistema conferir saldo e permitir reserva. A unidade precisa ser a mesma — não há conversão.',
    )

    unidade = models.CharField(max_length=6, choices=Unidade.choices, default=Unidade.UNIDADE)
    consumo = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal('0'),
        # 4 casas porque consumo de linha e de tecido é fracionado: 0,0125 kg
        # de linha por peça vira zero se arredondar em duas.
        help_text='Quanto entra em UMA peça, na unidade escolhida.',
        validators=[MinValueValidator(Decimal('0'))],
    )
    perda = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0'),
        help_text='Percentual de perda no corte/costura. Ex.: 8 para 8%.',
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
    )
    custo_unitario = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal('0'),
        help_text='Preço de UMA unidade do material.',
        validators=[MinValueValidator(Decimal('0'))],
    )

    observacao = models.CharField(max_length=160, blank=True)
    ordem = models.PositiveIntegerField(default=0)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'moda_fichas_materiais'
        ordering = ['ordem', 'id']
        indexes = [models.Index(fields=['ficha', 'tipo'])]
        verbose_name = 'Material da ficha'
        verbose_name_plural = 'Materiais da ficha'

    def __str__(self):
        return f'{self.get_tipo_display()} — {self.descricao}'

    # ── Cálculo ──────────────────────────────────────────────────────────

    @property
    def consumo_bruto(self) -> Decimal:
        """
        Consumo com a perda somada — é o que de fato sai do estoque.

        A perda entra como acréscimo sobre o consumo, e não como desconto do
        aproveitamento: 8% de perda em 1,20 m significa comprar 1,296 m, não
        1,104 m. As duas leituras aparecem no chão de fábrica e a diferença
        entre elas some do orçamento sem ninguém notar.
        """
        consumo = self.consumo or Decimal('0')
        perda = self.perda or Decimal('0')
        return (consumo * (Decimal('1') + perda / Decimal('100'))).quantize(Decimal('0.0001'))

    @property
    def custo_total(self) -> Decimal:
        """Custo deste material em uma peça, já com a perda."""
        return (self.consumo_bruto * (self.custo_unitario or Decimal('0'))).quantize(Decimal('0.01'))


class PesoTamanhoFicha(models.Model):
    """
    O peso de UM tamanho, de UMA grade, nesta ficha.

    Ex.: Ficha da camisa X, grade Adulto, tamanho G → 193 g. A mesma ficha
    pode ter uma segunda grade (Oversized) com pesos próprios, porque a
    camisa oversized não é a mesma peça escalada — é cortada diferente e
    pesa diferente tamanho a tamanho.

    `grade` e `tamanho` apontam para os cadastros normais de grade — este
    modelo não duplica sigla nem ordem, só acrescenta o peso que falta
    neles. Peso nulo (e não zero) é "ainda não pesado": a linha existe desde
    que a grade foi acrescentada à ficha, mas número nenhum foi digitado.
    """

    ficha = models.ForeignKey(
        FichaTecnica, on_delete=models.CASCADE, related_name='pesos_tamanho',
    )
    grade = models.ForeignKey(
        'moda.Grade', on_delete=models.PROTECT, related_name='+',
    )
    tamanho = models.ForeignKey(
        'moda.Tamanho', on_delete=models.PROTECT, related_name='+',
    )
    peso_g = models.DecimalField(
        max_digits=8, decimal_places=1, null=True, blank=True,
        verbose_name='Peso (g)',
        validators=[MinValueValidator(Decimal('0'))],
    )
    # Copiada do `ItemGrade.ordem` no momento em que a grade é acrescentada
    # à ficha -- assim a tabela mantém a ordem PP, P, M... mesmo que a
    # grade original seja reordenada depois.
    ordem = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'moda_fichas_pesos_tamanho'
        ordering = ['grade__nome', 'ordem', 'id']
        unique_together = [('ficha', 'grade', 'tamanho')]
        verbose_name = 'Peso por tamanho'
        verbose_name_plural = 'Pesos por tamanho'

    def __str__(self):
        return f'{self.grade.nome} {self.tamanho.sigla}: {self.peso_g if self.peso_g is not None else "—"} g'
