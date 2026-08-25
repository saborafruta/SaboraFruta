"""Modelo de Produto com dados comerciais, fiscais, industriais e logisticos."""
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import unquote, urlparse

from django.core.files.storage import default_storage
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel, TimestampedModel
from apps.cadastros.models import Fornecedor
from .categoria import CategoriaProduto
from .fiscal import ClasseFiscal
from .linha_producao import LinhaProducao
from .marca import MarcaProduto
from .unidade import UnidadeMedida


DIAS_SEMANA_TODOS = '0,1,2,3,4,5,6'


def _limitar_decimal(value, max_value, places):
    quantizer = Decimal('1').scaleb(-places)
    value = Decimal(value or 0)
    max_value = Decimal(max_value)
    min_value = -max_value
    if value > max_value:
        value = max_value
    elif value < min_value:
        value = min_value
    return value.quantize(quantizer, rounding=ROUND_HALF_UP)


class ProdutoManager(FilialManager):
    def for_filial(self, filial):
        if filial is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(
            filiais_vinculo__filial=filial,
            filiais_vinculo__ativo=True,
        ).distinct()

    def for_empresa(self, empresa):
        if empresa is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(
            filiais_vinculo__filial__empresa=empresa,
            filiais_vinculo__ativo=True,
        ).distinct()


class Produto(FilialScopedModel):
    class TipoProduto(models.TextChoices):
        UNITARIO = 'unitario', 'Unidade'
        FRACIONADO = 'fracionado', 'Fracionado'
        GRANEL_PESO = 'granel_peso', 'Granel (peso)'
        GRANEL_VOLUME = 'granel_volume', 'Granel (volume)'
        GRANEL_METRAGEM = 'granel_metragem', 'Granel (metragem)'
        SERVICO = 'servico', 'Servico'
        KIT = 'kit', 'Kit'

    class OrigemProduto(models.IntegerChoices):
        NACIONAL = 0, '0 - Nacional'
        IMPORTACAO_DIRETA = 1, '1 - Estrangeira importacao direta'
        ADQUIRIDA_MERCADO_INTERNO = 2, '2 - Estrangeira adquirida mercado interno'
        NACIONAL_CONTEUDO_IMPORTADO_40_70 = 3, '3 - Nacional, conteudo importado 40%-70%'
        NACIONAL_PROCESSO_BASICO = 4, '4 - Nacional, processo produtivo basico'
        NACIONAL_CONTEUDO_ATE_40 = 5, '5 - Nacional, conteudo importado ate 40%'
        ESTRANGEIRA_SEM_SIMILAR = 6, '6 - Estrangeira sem similar nacional'
        ESTRANGEIRA_ADQUIRIDA_MI_SEM_SIMILAR = 7, '7 - Estrangeira adquirida MI sem similar'
        NACIONAL_CONTEUDO_SUPERIOR_70 = 8, '8 - Nacional, conteudo importado superior 70%'

    class CondicaoArmazenamento(models.TextChoices):
        AMBIENTE = 'ambiente', 'Ambiente'
        REFRIGERADO = 'refrigerado', 'Refrigerado'
        CONGELADO = 'congelado', 'Congelado'
        SECO = 'seco', 'Seco'
        CONTROLADO = 'controlado', 'Controlado'

    class MetodoSaida(models.TextChoices):
        FEFO = 'fefo', 'FEFO (First Expired First Out)'
        FIFO = 'fifo', 'FIFO (First In First Out)'
        LIFO = 'lifo', 'LIFO (Last In First Out)'
        MANUAL = 'manual', 'Manual'

    class Moeda(models.TextChoices):
        BRL = 'BRL', 'Real (R$)'
        USD = 'USD', 'Dolar (US$)'
        EUR = 'EUR', 'Euro (EUR)'

    class UnidadePeso(models.TextChoices):
        KG = 'kg', 'Quilograma (kg)'
        G = 'g', 'Grama (g)'
        TON = 'ton', 'Tonelada (t)'

    class UnidadeDimensao(models.TextChoices):
        CM = 'cm', 'Centimetro (cm)'
        M = 'm', 'Metro (m)'
        MM = 'mm', 'Milimetro (mm)'

    categoria = models.ForeignKey(
        CategoriaProduto, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='produtos',
    )
    subcategoria = models.ForeignKey(
        CategoriaProduto, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='produtos_subcategoria',
    )
    linha_producao = models.ForeignKey(
        LinhaProducao, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='produtos',
    )
    marca = models.ForeignKey(
        MarcaProduto, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='produtos',
    )
    fornecedor = models.ForeignKey(
        Fornecedor, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='produtos',
    )
    unidade_medida = models.ForeignKey(
        UnidadeMedida, on_delete=models.PROTECT, related_name='produtos',
    )
    unidade_medida_compra = models.ForeignKey(
        UnidadeMedida, on_delete=models.PROTECT, null=True, blank=True,
        related_name='produtos_compra',
        help_text='Pode comprar em caixa e vender em unidade',
    )
    fator_conversao_compra = models.DecimalField(
        max_digits=14, decimal_places=6, default=1,
        help_text='Ex: 1 caixa = 12 unidades',
    )

    # Identificacao
    codigo = models.CharField(max_length=30, blank=True, help_text='Codigo interno')
    codigo_barras = models.CharField(max_length=14, blank=True, help_text='EAN-13 ou DUN-14')
    codigos_barras_extras = models.JSONField(
        default=list, blank=True, help_text='Array de codigos alternativos',
    )
    descricao = models.CharField(max_length=150)
    descricao_curta = models.CharField(max_length=120, blank=True)
    descricao_completa = models.TextField(blank=True)
    descricao_pdv = models.CharField(max_length=80, blank=True, help_text='Nome curto para PDV')

    # Fiscal
    ncm = models.CharField(max_length=8, help_text='NCM errado = rejeicao SEFAZ')
    cest = models.CharField(max_length=7, blank=True, help_text='Obrigatorio com ST')
    cfop_venda_interna = models.CharField(max_length=5, blank=True)
    cfop_venda_interestadual = models.CharField(max_length=5, blank=True)
    cfop_venda_exportacao = models.CharField(max_length=5, blank=True)
    cfop_devolucao = models.CharField(max_length=5, blank=True)
    cfop_devolucao_compra = models.CharField(max_length=5, blank=True)
    cfop_compra = models.CharField(max_length=5, blank=True)
    origem_produto = models.SmallIntegerField(
        choices=OrigemProduto.choices, default=OrigemProduto.NACIONAL,
    )
    classe_fiscal = models.ForeignKey(
        ClasseFiscal, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='produtos',
    )
    codigo_beneficio_fiscal_icms = models.CharField(
        max_length=20, blank=True, help_text='Codigo de beneficio fiscal quando exigido pela UF',
    )
    cst_csosn = models.CharField(max_length=3, blank=True)
    modalidade_bc_icms = models.CharField(max_length=2, blank=True)
    aliquota_icms = models.DecimalField(max_digits=7, decimal_places=4, default=0, blank=True)
    reducao_bc_icms = models.DecimalField(max_digits=7, decimal_places=4, default=0, blank=True)
    aliquota_fcp = models.DecimalField(max_digits=7, decimal_places=4, default=0, blank=True)
    modalidade_bc_icms_st = models.CharField(max_length=2, blank=True)
    mva_icms_st = models.DecimalField(max_digits=7, decimal_places=4, default=0, blank=True)
    reducao_bc_icms_st = models.DecimalField(max_digits=7, decimal_places=4, default=0, blank=True)
    aliquota_icms_st = models.DecimalField(max_digits=7, decimal_places=4, default=0, blank=True)
    aliquota_fcp_st = models.DecimalField(max_digits=7, decimal_places=4, default=0, blank=True)
    cst_pis = models.CharField(max_length=2, blank=True)
    aliquota_pis = models.DecimalField(max_digits=7, decimal_places=4, default=0, blank=True)
    cst_cofins = models.CharField(max_length=2, blank=True)
    aliquota_cofins = models.DecimalField(max_digits=7, decimal_places=4, default=0, blank=True)
    natureza_receita_pis_cofins = models.CharField(
        max_length=3, blank=True, help_text='Usado em regimes como monofasico, aliquota zero ou suspensao',
    )
    cst_ipi = models.CharField(max_length=2, blank=True)
    codigo_enquadramento_ipi = models.CharField(
        max_length=3, blank=True, help_text='Obrigatorio quando CST IPI = 99',
    )
    aliquota_ipi = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    ex_tipi = models.CharField(max_length=3, blank=True, help_text='Ex TIPI quando aplicavel')
    cst_cbs = models.CharField(max_length=3, blank=True)
    classificacao_tributaria_cbs = models.CharField(max_length=6, blank=True)
    aliquota_cbs = models.DecimalField(max_digits=7, decimal_places=4, default=0, blank=True)
    reducao_cbs = models.DecimalField(max_digits=7, decimal_places=4, default=0, blank=True)
    cst_ibs = models.CharField(max_length=3, blank=True)
    classificacao_tributaria_ibs = models.CharField(max_length=6, blank=True)
    aliquota_ibs_uf = models.DecimalField(max_digits=7, decimal_places=4, default=0, blank=True)
    aliquota_ibs_municipal = models.DecimalField(max_digits=7, decimal_places=4, default=0, blank=True)
    reducao_ibs = models.DecimalField(max_digits=7, decimal_places=4, default=0, blank=True)
    cst_is = models.CharField(max_length=3, blank=True)
    classificacao_tributaria_is = models.CharField(max_length=6, blank=True)
    aliquota_is = models.DecimalField(max_digits=7, decimal_places=4, default=0, blank=True)
    informacoes_complementares_fiscais = models.TextField(blank=True)
    beneficios_fiscais_observacoes = models.TextField(blank=True)

    # Tipo e granel
    tipo_produto = models.CharField(
        max_length=20, choices=TipoProduto.choices, default=TipoProduto.UNITARIO,
    )
    codigo_balanca = models.CharField(
        max_length=5, blank=True, help_text='Codigo para balanca (4-5 digitos)',
    )
    tara_padrao = models.DecimalField(
        max_digits=10, decimal_places=3, default=0,
        help_text='Tara em kg do recipiente padrao',
    )
    variacao_peso_permitida = models.DecimalField(
        max_digits=5, decimal_places=2, default=5,
        help_text='% variacao aceita na pesagem',
    )
    fracionavel = models.BooleanField(default=False)
    vendido_por_peso_granel = models.BooleanField(default=False)
    peso_minimo_venda = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    unidade_pesagem = models.CharField(
        max_length=10, choices=UnidadePeso.choices, default=UnidadePeso.KG,
    )
    gera_etiqueta_balanca = models.BooleanField(default=False)

    # Precos
    preco_custo = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    preco_custo_medio = models.DecimalField(
        max_digits=14, decimal_places=4, default=0,
        help_text='Calculado pelo sistema. Nunca editar manualmente.',
    )
    preco_venda = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    margem_lucro = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    moeda = models.CharField(max_length=3, choices=Moeda.choices, default=Moeda.BRL)
    margem_desejada = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    markup = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    preco_sugerido = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    preco_minimo = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    preco_promocional = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    promocao_tipo_desconto = models.CharField(
        max_length=20,
        choices=[
            ('preco_final', 'Preco final'),
            ('percentual', 'Percentual'),
            ('valor', 'Valor em R$'),
        ],
        default='preco_final',
    )
    promocao_valor_desconto = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    promocao_inicio = models.DateField(null=True, blank=True)
    promocao_fim = models.DateField(null=True, blank=True)
    promocao_dias_semana = models.CharField(max_length=13, default=DIAS_SEMANA_TODOS, blank=True)

    # Estoque
    estoque_minimo = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    estoque_maximo = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    ponto_reposicao = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    estoque_seguranca = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    lead_time_reposicao_dias = models.PositiveSmallIntegerField(default=0)
    metodo_saida = models.CharField(
        max_length=10, choices=MetodoSaida.choices, default=MetodoSaida.FEFO,
    )
    controla_lote = models.BooleanField(default=False)
    controla_validade = models.BooleanField(default=False)
    dias_aviso_vencimento = models.SmallIntegerField(
        default=30, help_text='Avisar X dias antes do vencimento',
    )
    saida_fefo = models.BooleanField(
        default=True, help_text='First Expired First Out - critico para embalagens',
    )
    permite_venda_sem_estoque = models.BooleanField(default=True)

    # Fisico e logistica
    peso_bruto = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    peso_liquido = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    largura = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    altura = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    profundidade = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    unidade_peso = models.CharField(
        max_length=10, choices=UnidadePeso.choices, default=UnidadePeso.KG,
    )
    unidade_dimensao = models.CharField(
        max_length=10, choices=UnidadeDimensao.choices, default=UnidadeDimensao.CM,
    )
    tipo_embalagem = models.CharField(max_length=60, blank=True)
    quantidade_por_embalagem = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    empilhamento_maximo = models.PositiveSmallIntegerField(default=0)
    localizacao_estoque = models.CharField(
        max_length=30, blank=True, help_text='Corredor/Prateleira/Posicao',
    )
    foto_url = models.URLField(max_length=500, blank=True)
    # Usado pela fila inteligente do KDS (apps.food_service) para calcular o
    # prazo de cada item -- quando em branco, o KDS assume um tempo padrão.
    tempo_preparo_minutos = models.PositiveSmallIntegerField(null=True, blank=True)

    # Industrial
    condicao_armazenamento = models.CharField(
        max_length=20, choices=CondicaoArmazenamento.choices,
        default=CondicaoArmazenamento.AMBIENTE,
    )
    temperatura_minima = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    temperatura_maxima = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    umidade_relativa = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    especificacoes_tecnicas = models.JSONField(default=list, blank=True)

    id_externo = models.CharField(max_length=100, blank=True, db_index=True)
    observacao = models.TextField(blank=True)
    rascunho_comercial = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Produto criado por entrada/XML que ainda precisa de revisao comercial antes de venda/PDV.',
    )
    ativo = models.BooleanField(default=True, db_index=True)

    objects = ProdutoManager()

    class Meta:
        db_table = 'produtos'
        ordering = ['descricao']
        verbose_name = 'Produto'
        indexes = [
            models.Index(fields=['filial', 'ativo']),
            models.Index(fields=['filial', 'codigo']),
            models.Index(fields=['filial', 'codigo_barras']),
            models.Index(fields=['filial', 'descricao']),
        ]

    def __str__(self):
        if self.codigo:
            return f'[{self.codigo}] {self.descricao}'
        return self.descricao

    @property
    def foto_url_resolvida(self):
        """Retorna uma URL vigente para fotos armazenadas no bucket privado."""
        valor = (self.foto_url or '').strip()
        if not valor:
            return ''

        parsed = urlparse(valor)
        nome_arquivo = ''
        if not parsed.scheme and not parsed.netloc:
            nome_arquivo = valor.lstrip('/')
        elif (
            parsed.path.lstrip('/').startswith('produtos/imagens/')
            and any(chave.lower().startswith('x-amz-') for chave in parsed.query.split('&'))
        ):
            nome_arquivo = unquote(parsed.path.lstrip('/'))

        if not nome_arquivo:
            return valor
        try:
            return default_storage.url(nome_arquivo)
        except (NotImplementedError, ValueError):
            return valor

    @property
    def codigo_replicacao(self):
        """Codigo visual compartilhado entre filiais para produtos replicados."""
        if self.id_externo:
            sufixo = self.id_externo.rsplit(':', 1)[-1]
            if sufixo.isdigit():
                return int(sufixo)
        return self.pk

    @property
    def eh_granel(self):
        return self.tipo_produto in (
            self.TipoProduto.GRANEL_PESO,
            self.TipoProduto.GRANEL_VOLUME,
            self.TipoProduto.GRANEL_METRAGEM,
        )

    @property
    def promocao_ativa(self):
        from apps.produtos.services.preco_service import PrecoService
        return PrecoService.produto_tem_promocao_vigente(self)

    @property
    def preco_atual(self):
        from apps.produtos.services.preco_service import PrecoService
        return PrecoService.preco_vivo_produto(self)

    @property
    def preco_promocional_atual(self):
        from apps.produtos.services.preco_service import PrecoService
        return PrecoService.calcular_preco_promocional(self)

    @property
    def preco_retorno_promocao(self):
        return self.preco_venda

    @property
    def margem_atual(self):
        cem = Decimal('100')
        base_custo = self.preco_custo_medio or self.preco_custo or Decimal('0')
        preco = self.preco_atual or Decimal('0')
        if preco > 0:
            return ((preco - base_custo) / preco) * cem
        return Decimal('0')

    @property
    def markup_atual(self):
        cem = Decimal('100')
        base_custo = self.preco_custo_medio or self.preco_custo or Decimal('0')
        preco = self.preco_atual or Decimal('0')
        if base_custo > 0:
            return ((preco - base_custo) / base_custo) * cem
        return Decimal('0')

    @property
    def volume_cubagem(self):
        if self.largura and self.altura and self.profundidade:
            divisor = 1000000
            if self.unidade_dimensao == self.UnidadeDimensao.M:
                divisor = 1
            elif self.unidade_dimensao == self.UnidadeDimensao.MM:
                divisor = 1000000000
            return (self.largura * self.altura * self.profundidade) / divisor
        return 0

    def calcular_margem(self):
        """Recalcula margem, markup e preco sugerido com base no custo."""
        cem = Decimal('100')
        base_custo = self.preco_custo_medio or self.preco_custo or Decimal('0')
        preco_venda = self.preco_venda or Decimal('0')

        if preco_venda > 0:
            margem_lucro = ((preco_venda - base_custo) / preco_venda) * cem
            self.margem_lucro = _limitar_decimal(margem_lucro, Decimal('999.99'), 2)
        else:
            self.margem_lucro = Decimal('0')

        if base_custo > 0:
            markup = ((preco_venda - base_custo) / base_custo) * cem
            self.markup = _limitar_decimal(markup, Decimal('9999.9999'), 4)
        else:
            self.markup = Decimal('0')

        margem_desejada = self.margem_desejada or Decimal('0')
        if self.preco_custo and margem_desejada > 0 and margem_desejada < cem:
            preco_sugerido = self.preco_custo / (Decimal('1') - (margem_desejada / cem))
            self.preco_sugerido = _limitar_decimal(preco_sugerido, Decimal('9999999999.9999'), 4)
        else:
            self.preco_sugerido = Decimal('0')


class ProdutoFilial(TimestampedModel):
    produto = models.ForeignKey(Produto, on_delete=models.CASCADE, related_name='filiais_vinculo')
    filial = models.ForeignKey('core.Filial', on_delete=models.CASCADE, related_name='produtos_vinculados')
    ativo = models.BooleanField(default=True, db_index=True)
    preco_promocional = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    preco_promocional_ativo = models.BooleanField(default=True)
    preco_promocional_replicar_filiais = models.BooleanField(default=False)
    promocao_tipo_desconto = models.CharField(max_length=20, default='preco_final', blank=True)
    promocao_valor_desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    promocao_inicio = models.DateField(null=True, blank=True)
    promocao_fim = models.DateField(null=True, blank=True)
    promocao_dias_semana = models.CharField(max_length=20, default=DIAS_SEMANA_TODOS, blank=True)

    class Meta:
        db_table = 'produtos_filiais'
        ordering = ['produto', 'filial']
        unique_together = [('produto', 'filial')]
        indexes = [
            models.Index(fields=['filial', 'ativo']),
            models.Index(fields=['produto', 'ativo']),
        ]

    def __str__(self):
        return f'{self.produto} - {self.filial}'
