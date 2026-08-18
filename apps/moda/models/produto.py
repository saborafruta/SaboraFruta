"""
Produto de confecção e suas variantes.

Estrutura: Produto → Cor → Tamanho → SKU.

O produto é o modelo comercial ("Camiseta Esportiva"). Cada cor que ele
sai vira um `ProdutoCor`, e cada cruzamento cor × tamanho da grade vira uma
`Variante`, que é a linha com SKU — o que de fato se produz, se estoca e se
etiqueta. Quem gera as variantes é
`apps/moda/services/variantes.py`.
"""
from django.db import models

from apps.core.models.base import ActiveModel, FilialManager, FilialScopedModel


class ProdutoModa(FilialScopedModel, ActiveModel):
    """Produto do catálogo de confecção."""

    class Status(models.TextChoices):
        RASCUNHO = 'rascunho', 'Rascunho'
        ATIVO = 'ativo', 'Ativo'
        DESCONTINUADO = 'descontinuado', 'Descontinuado'

    # ── Identificação ────────────────────────────────────────────────────
    codigo = models.CharField(
        max_length=30,
        help_text='Código interno. Vira o prefixo do SKU das variantes.',
    )
    referencia = models.CharField(
        max_length=40, blank=True,
        help_text='Referência comercial/do cliente, quando difere do código.',
    )
    nome = models.CharField(max_length=120)
    descricao = models.TextField(blank=True)

    # ── Classificação ────────────────────────────────────────────────────
    categoria = models.ForeignKey(
        'moda.Categoria', on_delete=models.PROTECT, null=True, blank=True,
        related_name='produtos',
        help_text='Categoria raiz ou subcategoria — as duas vivem no mesmo cadastro.',
    )
    colecao = models.ForeignKey(
        'moda.Colecao', on_delete=models.PROTECT, null=True, blank=True,
        related_name='produtos',
    )
    linha = models.ForeignKey(
        'moda.Linha', on_delete=models.PROTECT, null=True, blank=True,
        related_name='produtos',
    )
    modelo = models.ForeignKey(
        'moda.Modelo', on_delete=models.PROTECT, null=True, blank=True,
        related_name='produtos',
        help_text='Modelagem base — é dela que vêm gola e manga.',
    )
    marca = models.ForeignKey(
        'moda.Marca', on_delete=models.PROTECT, null=True, blank=True,
        related_name='produtos',
    )

    # ── Matéria-prima ────────────────────────────────────────────────────
    # Composição e gramatura não se repetem aqui: são lidas do tecido, via
    # as properties abaixo. Ver a justificativa em models/cadastros.py.
    tecido = models.ForeignKey(
        'moda.Tecido', on_delete=models.PROTECT, null=True, blank=True,
        related_name='produtos',
    )

    # ── Grade ────────────────────────────────────────────────────────────
    grade = models.ForeignKey(
        'moda.Grade', on_delete=models.PROTECT, null=True, blank=True,
        related_name='produtos',
        help_text='Define quais tamanhos viram variante.',
    )

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RASCUNHO,
    )

    # ── Anexos ───────────────────────────────────────────────────────────
    foto = models.ImageField(upload_to='moda/produtos/fotos/', blank=True, null=True)
    desenho_tecnico = models.FileField(
        upload_to='moda/produtos/desenhos/', blank=True, null=True,
        help_text='Desenho técnico da peça (imagem ou PDF).',
    )
    ficha_tecnica = models.FileField(
        upload_to='moda/produtos/fichas/', blank=True, null=True,
        help_text='Ficha técnica em arquivo. A ficha estruturada vem no módulo Engenharia.',
    )

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_produtos'
        ordering = ['nome']
        unique_together = [('filial', 'codigo')]
        indexes = [
            models.Index(fields=['filial', 'status']),
            models.Index(fields=['filial', 'nome']),
        ]
        verbose_name = 'Produto de moda'
        verbose_name_plural = 'Produtos de moda'

    def __str__(self):
        return f'{self.codigo} — {self.nome}'

    def save(self, *args, **kwargs):
        self.codigo = (self.codigo or '').strip().upper()
        super().save(*args, **kwargs)

    # Composição e gramatura vêm do tecido — o cadastro do produto as
    # mostra, mas não as guarda, para não haver duas versões da verdade.
    @property
    def composicao(self) -> str:
        return self.tecido.composicao if self.tecido_id else ''

    @property
    def gramatura(self):
        return self.tecido.gramatura if self.tecido_id else None

    @property
    def gola(self) -> str:
        return self.modelo.get_gola_display() if self.modelo_id and self.modelo.gola else ''

    @property
    def manga(self) -> str:
        return self.modelo.get_manga_display() if self.modelo_id and self.modelo.manga else ''

    @property
    def total_variantes(self) -> int:
        return self.variantes.count()


class ProdutoCor(models.Model):
    """
    Uma cor em que o produto é produzido.

    Existe como tabela própria, em vez de um M2M direto, porque a cor
    carrega dados próprios do par produto+cor: foto daquela cor e o código
    de referência que alguns clientes exigem por cor.
    """

    produto = models.ForeignKey(
        ProdutoModa, on_delete=models.CASCADE, related_name='cores',
    )
    cor = models.ForeignKey(
        'moda.Cor', on_delete=models.PROTECT, related_name='produtos',
    )
    referencia_cor = models.CharField(max_length=40, blank=True)
    foto = models.ImageField(upload_to='moda/produtos/cores/', blank=True, null=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        db_table = 'moda_produto_cores'
        ordering = ['cor__nome']
        unique_together = [('produto', 'cor')]
        verbose_name = 'Cor do produto'
        verbose_name_plural = 'Cores do produto'

    def __str__(self):
        return f'{self.produto.codigo} {self.cor.nome}'


class Variante(models.Model):
    """
    O cruzamento produto × cor × tamanho — a linha que tem SKU.

    É o nível em que a produção, o estoque e a etiqueta trabalham: não se
    produz "Camiseta Esportiva", produz-se "Camiseta Esportiva amarela M".
    """

    produto = models.ForeignKey(
        ProdutoModa, on_delete=models.CASCADE, related_name='variantes',
    )
    produto_cor = models.ForeignKey(
        ProdutoCor, on_delete=models.CASCADE, related_name='variantes',
    )
    tamanho = models.ForeignKey(
        'moda.Tamanho', on_delete=models.PROTECT, related_name='variantes',
    )
    sku = models.CharField(max_length=60, db_index=True)
    codigo_barras = models.CharField(max_length=60, blank=True)
    ativo = models.BooleanField(default=True)

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'moda_variantes'
        # A ordem do tamanho manda: a lista de variantes é lida como grade
        # (PP, P, M, G...), então ordenar por sigla deixaria a tela errada.
        ordering = ['produto_cor__cor__nome', 'tamanho__ordem', 'tamanho__sigla']
        unique_together = [
            ('produto', 'produto_cor', 'tamanho'),
            # SKU único por filial, não global: duas filiais podem ter
            # códigos iguais sem colidir.
            ('produto', 'sku'),
        ]
        indexes = [models.Index(fields=['sku'])]
        verbose_name = 'Variante'
        verbose_name_plural = 'Variantes'

    def __str__(self):
        return self.sku
