"""
Cadastros de apoio do vertical Moda: as tabelas que o produto referencia.

Todos seguem o mesmo desenho: escopo por filial, `nome` único dentro da
filial e `ativo` para desativar sem apagar (apagar quebraria produtos que
já apontam para o registro).
"""
from django.db import models

from apps.core.models.base import ActiveModel, FilialManager, FilialScopedModel


class CadastroApoio(FilialScopedModel, ActiveModel):
    """Base dos cadastros simples — só nome e observação."""

    nome = models.CharField(max_length=80)
    observacao = models.TextField(blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True
        ordering = ['nome']

    def __str__(self):
        return self.nome


class Marca(CadastroApoio):
    class Meta(CadastroApoio.Meta):
        abstract = False
        db_table = 'moda_marcas'
        ordering = ['nome']
        unique_together = [('filial', 'nome')]
        verbose_name = 'Marca'
        verbose_name_plural = 'Marcas'


class Linha(CadastroApoio):
    """Linha de produto (ex.: Esportiva, Casual, Uniforme profissional)."""

    class Meta(CadastroApoio.Meta):
        abstract = False
        db_table = 'moda_linhas'
        ordering = ['nome']
        unique_together = [('filial', 'nome')]
        verbose_name = 'Linha'
        verbose_name_plural = 'Linhas'


class Colecao(CadastroApoio):
    """Agrupamento por temporada ou campanha."""

    ano = models.PositiveIntegerField(null=True, blank=True)
    estacao = models.CharField(max_length=40, blank=True)
    data_inicio = models.DateField(null=True, blank=True)
    data_fim = models.DateField(null=True, blank=True)

    class Meta(CadastroApoio.Meta):
        abstract = False
        db_table = 'moda_colecoes'
        ordering = ['-ano', 'nome']
        unique_together = [('filial', 'nome')]
        verbose_name = 'Coleção'
        verbose_name_plural = 'Coleções'

    def __str__(self):
        return f'{self.nome} {self.ano}' if self.ano else self.nome


class Categoria(CadastroApoio):
    """
    Categoria e subcategoria no mesmo model, via auto-relacionamento.

    Duas tabelas separadas obrigariam a duplicar o CRUD inteiro e travariam
    o cadastro em exatamente dois níveis. Com `pai`, subcategoria é uma
    categoria com pai — e um terceiro nível, se um dia precisar, não custa
    migration.
    """

    pai = models.ForeignKey(
        'self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='subcategorias',
        help_text='Deixe vazio para categoria raiz; preencha para subcategoria.',
    )

    class Meta(CadastroApoio.Meta):
        abstract = False
        db_table = 'moda_categorias'
        ordering = ['nome']
        unique_together = [('filial', 'pai', 'nome')]
        verbose_name = 'Categoria'
        verbose_name_plural = 'Categorias'

    def __str__(self):
        return f'{self.pai.nome} › {self.nome}' if self.pai_id else self.nome

    @property
    def e_subcategoria(self) -> bool:
        return self.pai_id is not None


class Cor(CadastroApoio):
    """Cartela de cores. `sigla` entra na composição do SKU."""

    sigla = models.CharField(
        max_length=6,
        help_text='Abreviação usada no SKU (ex.: AMA para Amarelo).',
    )
    # Hex para a tela mostrar a cor de verdade, em vez de só o nome --
    # "Azul Royal" e "Azul Marinho" são indistinguíveis numa lista de texto.
    hex_cor = models.CharField(
        max_length=7, blank=True,
        help_text='Cor em hexadecimal (#RRGGBB), para exibição.',
    )
    codigo_pantone = models.CharField(max_length=30, blank=True)

    class Meta(CadastroApoio.Meta):
        abstract = False
        db_table = 'moda_cores'
        ordering = ['nome']
        unique_together = [('filial', 'nome'), ('filial', 'sigla')]
        verbose_name = 'Cor'
        verbose_name_plural = 'Cores'

    def save(self, *args, **kwargs):
        self.sigla = (self.sigla or '').strip().upper()
        super().save(*args, **kwargs)


class Tecido(CadastroApoio):
    """
    Tecido/malha (Dry, Piquet, Malha PV...).

    Composição e gramatura ficam aqui, e não no produto: são propriedades
    do tecido. Repetidas em cada produto, 50 produtos com o mesmo Dry
    exigiriam 50 edições para corrigir a composição — e na prática elas
    divergiriam. O cadastro do produto exibe os dois campos a partir daqui.
    """

    composicao = models.CharField(
        max_length=120, blank=True,
        help_text='Ex.: 100% Poliéster; 67% Poliéster 33% Algodão.',
    )
    gramatura = models.PositiveIntegerField(
        null=True, blank=True, help_text='Em g/m².',
    )
    largura_cm = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text='Largura útil do rolo, usada no cálculo de encaixe.',
    )
    fornecedor = models.ForeignKey(
        'cadastros.Fornecedor', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='tecidos_moda',
    )

    # A ponte com o estoque real. Sem ela o tecido é só um nome de catálogo
    # e não há de onde ler saldo -- a tela de Estoque › Tecidos consegue
    # deduzir o vínculo pelas fichas dos produtos, mas só enquanto existir
    # produto com ficha usando este tecido. Aqui o vínculo é do PRÓPRIO
    # tecido, e vale mesmo para o rolo que ainda não entrou em ficha
    # nenhuma.
    produto_estoque = models.ForeignKey(
        'produtos.Produto', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tecidos_moda',
        verbose_name='Produto no estoque',
        help_text=(
            'Ligue para o sistema ler o saldo em metros. A unidade precisa '
            'ser a mesma — não há conversão.'
        ),
    )

    class Meta(CadastroApoio.Meta):
        abstract = False
        db_table = 'moda_tecidos'
        ordering = ['nome']
        unique_together = [('filial', 'nome')]
        verbose_name = 'Tecido'
        verbose_name_plural = 'Tecidos'

    def __str__(self):
        if self.gramatura:
            return f'{self.nome} ({self.gramatura} g/m²)'
        return self.nome


class Modelo(CadastroApoio):
    """
    Modelagem base reaproveitada entre produtos (ex.: Camisa gola careca
    manga curta). Os atributos que a ficha de produção destaca -- gola,
    manga -- ficam aqui, porque são da modelagem, não da cor ou do tamanho.
    """

    class Gola(models.TextChoices):
        CARECA = 'careca', 'Careca'
        POLO = 'polo', 'Polo'
        V = 'v', 'V'
        FRADE = 'frade', 'Frade'
        REDONDA = 'redonda', 'Redonda'
        OUTRA = 'outra', 'Outra'

    class Manga(models.TextChoices):
        SEM_MANGA = 'sem_manga', 'Sem manga'
        CURTA = 'curta', 'Curta'
        LONGA = 'longa', 'Longa'
        COM_PUNHO = 'com_punho', 'Com punho'
        RAGLAN = 'raglan', 'Raglan'
        OUTRA = 'outra', 'Outra'

    gola = models.CharField(max_length=20, choices=Gola.choices, blank=True)
    manga = models.CharField(max_length=20, choices=Manga.choices, blank=True)

    class Meta(CadastroApoio.Meta):
        abstract = False
        db_table = 'moda_modelos'
        ordering = ['nome']
        unique_together = [('filial', 'nome')]
        verbose_name = 'Modelo'
        verbose_name_plural = 'Modelos'
