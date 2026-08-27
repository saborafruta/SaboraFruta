"""
Entrada de Nota Fiscal — recebimento físico da mercadoria.
Sempre vinculada (opcionalmente) a um Pedido de Compra.
"""
from decimal import Decimal

from django.db import models

from apps.core.models.base import FilialScopedModel, TimestampedModel


class EntradaNF(FilialScopedModel):

    class Status(models.TextChoices):
        RASCUNHO = 'rascunho', 'Rascunho'
        AGUARDANDO_VINCULOS = 'aguardando_vinculos', 'Aguardando vinculos'
        AGUARDANDO_CONFERENCIA = 'aguardando_conferencia', 'Aguardando conferencia'
        COM_DIFERENCAS = 'com_diferencas', 'Com diferencas'
        CONFERIDA = 'conferida', 'Conferida'
        PROCESSANDO = 'processando', 'Processando'
        EFETIVADA = 'efetivada', 'Efetivada'
        CANCELADA = 'cancelada', 'Cancelada'
        ESTORNADA = 'estornada', 'Estornada'

    class TipoNota(models.TextChoices):
        ENTRADA = 'entrada', 'Entrada'
        DEVOLUCAO = 'devolucao', 'Devolução de Cliente'
        TRANSFERENCIA = 'transferencia', 'Transferência'

    # Vinculação opcional com pedido
    class OrigemEntrada(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        XML = 'xml', 'XML'
        CHAVE = 'chave', 'Chave de acesso'
        MANIFESTO = 'manifesto', 'Manifesto fiscal'

    class MetodoRateioCusto(models.TextChoices):
        VALOR = 'valor', 'Valor dos itens'
        QUANTIDADE = 'quantidade', 'Quantidade'
        PESO = 'peso', 'Peso'

    pedido_compra = models.ForeignKey(
        'compras.PedidoCompra', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='entradas',
    )
    fornecedor = models.ForeignKey(
        'cadastros.Fornecedor', on_delete=models.PROTECT, related_name='entradas_nf',
    )

    # Dados da NF do fornecedor
    numero_nf = models.CharField(max_length=20, db_index=True)
    serie_nf = models.CharField(max_length=5, default='1')
    chave_acesso_nf = models.CharField(
        max_length=44, blank=True, db_index=True,
        help_text='Chave de 44 dígitos da NF-e do fornecedor',
    )
    origem_entrada = models.CharField(
        max_length=20,
        choices=OrigemEntrada.choices,
        default=OrigemEntrada.MANUAL,
        db_index=True,
    )
    xml_original = models.TextField(blank=True)
    xml_nome_arquivo = models.CharField(max_length=180, blank=True)
    destinatario_documento_xml = models.CharField(max_length=18, blank=True)
    destinatario_nome_xml = models.CharField(max_length=180, blank=True)
    destinatario_documento_diferente = models.BooleanField(
        default=False,
        help_text='Apenas alerta operacional; nao bloqueia a entrada.',
    )
    data_emissao_nf = models.DateField()
    data_entrada = models.DateTimeField(db_index=True)

    tipo = models.CharField(
        max_length=20, choices=TipoNota.choices, default=TipoNota.ENTRADA,
    )
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.RASCUNHO, db_index=True,
    )

    # Valores (snapshot da NF do fornecedor)
    valor_produtos = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_frete = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_seguro = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_outras_despesas = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_ipi = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_icms = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_icms_st = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_pis = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_cofins = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # Composicao do custo de entrada usada para custo medio e custo por lote.
    custo_rateio_metodo = models.CharField(
        max_length=20,
        choices=MetodoRateioCusto.choices,
        default=MetodoRateioCusto.VALOR,
    )
    custo_incluir_ipi = models.BooleanField(default=True)
    custo_incluir_icms_st = models.BooleanField(default=True)
    custo_incluir_icms = models.BooleanField(
        default=False,
        help_text='Marcar apenas quando o ICMS normal nao for recuperavel.',
    )
    custo_financeiro = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    custo_composto_em = models.DateTimeField(null=True, blank=True)

    # Controle
    usuario = models.ForeignKey(
        'core.Usuario', on_delete=models.PROTECT, related_name='entradas_criadas',
    )
    usuario_efetivacao = models.ForeignKey(
        'core.Usuario', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='entradas_efetivadas',
    )
    data_efetivacao = models.DateTimeField(null=True, blank=True)
    usuario_estorno = models.ForeignKey(
        'core.Usuario', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='entradas_estornadas',
    )
    data_estorno = models.DateTimeField(null=True, blank=True)
    fornecedor_pendente = models.BooleanField(default=False, db_index=True)
    emitente_cnpj_xml = models.CharField(max_length=18, blank=True, db_index=True)
    emitente_razao_social_xml = models.CharField(max_length=180, blank=True)
    emitente_nome_fantasia_xml = models.CharField(max_length=180, blank=True)
    emitente_ie_xml = models.CharField(max_length=30, blank=True)
    emitente_endereco_xml = models.CharField(max_length=255, blank=True)
    emitente_municipio_xml = models.CharField(max_length=120, blank=True)
    emitente_uf_xml = models.CharField(max_length=2, blank=True)
    emitente_cep_xml = models.CharField(max_length=10, blank=True)
    emitente_telefone_xml = models.CharField(max_length=30, blank=True)
    observacao = models.TextField(blank=True)

    # Flags operacionais (adicionados em mai/2026)
    class OrigemFiscal(models.TextChoices):
        NACIONAL = 'nacional', 'Nacional'
        IMPORTACAO = 'importacao', 'Importacao'

    class TipoEntradaOperacional(models.TextChoices):
        COMPRA_REVENDA = 'compra_revenda', 'Compra para revenda'
        COMPRA_PRODUCAO = 'compra_producao', 'Compra para producao'
        USO_CONSUMO = 'uso_consumo', 'Uso e consumo'
        ATIVO_IMOBILIZADO = 'ativo_imobilizado', 'Ativo imobilizado'
        SERVICO_DESPESA = 'servico_despesa', 'Servico / despesa'
        BONIFICACAO_AMOSTRA = 'bonificacao_amostra', 'Bonificacao / amostra'
        CONSIGNACAO = 'consignacao', 'Consignacao'

    origem_fiscal = models.CharField(
        max_length=20, choices=OrigemFiscal.choices,
        default=OrigemFiscal.NACIONAL, db_index=True,
    )
    tipo_entrada_operacional = models.CharField(
        max_length=30, choices=TipoEntradaOperacional.choices,
        default=TipoEntradaOperacional.COMPRA_REVENDA, db_index=True,
    )
    movimenta_estoque = models.BooleanField(default=True)
    movimenta_financeiro = models.BooleanField(default=True)
    altera_custo_estoque = models.BooleanField(default=True)

    class Meta:
        db_table = 'entradas_nf'
        ordering = ['-data_entrada']
        unique_together = [('fornecedor', 'numero_nf', 'serie_nf', 'filial')]
        constraints = [
            models.UniqueConstraint(
                fields=['filial', 'chave_acesso_nf'],
                condition=~models.Q(chave_acesso_nf=''),
                name='uniq_entrada_nf_chave_por_filial',
            ),
        ]
        indexes = [
            models.Index(fields=['filial', 'status']),
            models.Index(fields=['chave_acesso_nf']),
            models.Index(fields=['filial', 'origem_entrada'], name='entradas_nf_filial_origem_idx'),
            models.Index(fields=['filial', 'fornecedor_pendente'], name='entradas_nf_fornpend_idx'),
        ]
        verbose_name = 'Entrada de NF'
        verbose_name_plural = 'Entradas de NF'

    def __str__(self):
        return f'NF {self.numero_nf}/{self.serie_nf} — {self.fornecedor}'

    @property
    def pode_efetivar(self):
        return self.status in (
            self.Status.RASCUNHO,
            self.Status.AGUARDANDO_CONFERENCIA,
            self.Status.COM_DIFERENCAS,
            self.Status.CONFERIDA,
        )

    @property
    def pode_cancelar(self):
        return self.status in (
            self.Status.RASCUNHO,
            self.Status.AGUARDANDO_VINCULOS,
            self.Status.AGUARDANDO_CONFERENCIA,
            self.Status.COM_DIFERENCAS,
            self.Status.CONFERIDA,
        )

    @property
    def pode_estornar(self):
        return self.status == self.Status.EFETIVADA

    @property
    def fornecedor_nome_display(self):
        if self.fornecedor_pendente and self.emitente_razao_social_xml:
            return self.emitente_razao_social_xml
        return str(self.fornecedor)

    @property
    def total_ajustes_financeiros(self):
        total = Decimal('0')
        for ajuste in self.ajustes_financeiros.all():
            total += ajuste.valor_assinado
        return total.quantize(Decimal('0.01'))

    @property
    def valor_total_financeiro(self):
        """
        O que o financeiro cobra: o valor da NF mais os ajustes.

        E' a referencia contra a qual as parcelas precisam fechar. A
        propriedade tinha sumido do modelo e o servico continuou a chama-la --
        gerar contas a pagar de qualquer entrada morria em AttributeError.
        """
        return (self.valor_total + self.total_ajustes_financeiros).quantize(Decimal('0.01'))


class EntradaNFParcela(TimestampedModel):
    """Parcela prevista para o contas a pagar da entrada, ainda sem lancar financeiro."""

    class Origem(models.TextChoices):
        XML = 'xml', 'XML'
        MANUAL = 'manual', 'Manual'

    class Status(models.TextChoices):
        PENDENTE = 'pendente', 'Pendente'
        GERADA = 'gerada', 'Conta gerada'
        CANCELADA = 'cancelada', 'Cancelada'

    entrada = models.ForeignKey(
        EntradaNF, on_delete=models.CASCADE, related_name='parcelas_financeiras',
    )
    numero = models.CharField(max_length=40, blank=True)
    data_vencimento = models.DateField(null=True, blank=True, db_index=True)
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    forma_pagamento = models.CharField(max_length=40, blank=True)
    origem = models.CharField(max_length=20, choices=Origem.choices, default=Origem.MANUAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDENTE, db_index=True)
    fornecedor_pendente = models.BooleanField(default=False, db_index=True)
    emitente_documento_xml = models.CharField(max_length=18, blank=True)
    emitente_nome_xml = models.CharField(max_length=180, blank=True)
    conta_pagar_id = models.BigIntegerField(null=True, blank=True)
    observacao = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'entradas_nf_parcelas'
        ordering = ['entrada', 'data_vencimento', 'numero']
        indexes = [
            models.Index(fields=['entrada', 'status'], name='entrada_parcela_status_idx'),
            models.Index(fields=['entrada', 'origem'], name='entrada_parcela_origem_idx'),
        ]
        verbose_name = 'Parcela da entrada'
        verbose_name_plural = 'Parcelas da entrada'

    def __str__(self):
        numero = self.numero or self.pk or ''
        return f'Parcela {numero} - NF {self.entrada.numero_nf}'


class ItemEntradaNF(TimestampedModel):
    """
    Linha de produto recebido. Cada item PODE vincular a um item de pedido de compra
    (para marcar quantidade_recebida) e SEMPRE gera lote no estoque ao efetivar.
    """

    entrada = models.ForeignKey(
        EntradaNF, on_delete=models.CASCADE, related_name='itens',
    )
    item_pedido_compra = models.ForeignKey(
        'compras.ItemPedidoCompra', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='itens_entrada',
        help_text='Link ao item do pedido original (para abater quantidade)',
    )
    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.PROTECT, null=True, blank=True,
        related_name='+',
    )
    numero_item = models.SmallIntegerField(default=1)

    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    quantidade_xml = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    quantidade_estoque = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    quantidade_recebida = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    unidade_xml = models.CharField(max_length=10, blank=True)
    unidade_estoque = models.CharField(max_length=10, blank=True)
    fator_conversao = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=4)
    custo_unitario_total = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=0,
        help_text='Campo legado: custo unitario final com rateios.',
    )
    valor_bruto = models.DecimalField(max_digits=14, decimal_places=2)
    valor_desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_ipi = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_icms = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2)

    # Lote (se produto controla)
    numero_lote = models.CharField(max_length=60, blank=True)
    data_fabricacao = models.DateField(null=True, blank=True)
    data_validade = models.DateField(null=True, blank=True)
    ean_xml = models.CharField(max_length=32, blank=True, db_index=True)
    ncm_xml = models.CharField(max_length=8, blank=True, db_index=True)
    codigo_produto_fornecedor = models.CharField(max_length=80, blank=True)
    descricao_xml = models.CharField(max_length=255, blank=True)
    diferenca_tipo = models.CharField(max_length=40, blank=True)
    diferenca_descricao = models.CharField(max_length=255, blank=True)
    diferenca_bloqueante = models.BooleanField(default=False)
    justificativa_diferenca = models.TextField(blank=True)

    # Referência ao lote criado ao efetivar
    lote_gerado = models.ForeignKey(
        'estoque.LoteProduto', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )

    observacao = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'itens_entrada_nf'
        ordering = ['entrada', 'numero_item']
        indexes = [
            models.Index(fields=['ean_xml'], name='itens_entrada_ean_xml_idx'),
            models.Index(fields=['entrada', 'diferenca_bloqueante'], name='itens_entrada_diff_block_idx'),
        ]

    def calcular_totais(self):
        if not self.quantidade_xml:
            self.quantidade_xml = self.quantidade
        if not self.fator_conversao:
            self.fator_conversao = 1
        if not self.quantidade_estoque:
            self.quantidade_estoque = self.quantidade_xml * self.fator_conversao
        if not self.quantidade_recebida:
            self.quantidade_recebida = self.quantidade_estoque
        self.quantidade = self.quantidade_estoque
        self.valor_bruto = self.quantidade * self.valor_unitario
        self.valor_total = self.valor_bruto - self.valor_desconto + self.valor_ipi
        ipi_unitario = (self.valor_ipi / self.quantidade) if self.valor_ipi and self.quantidade else 0
        self.custo_unitario_total = self.valor_unitario + ipi_unitario


class ItemEntradaNFProdutoGerado(TimestampedModel):
    """Produto gerado a partir de um item da entrada (ex: fracionamento)."""

    item = models.ForeignKey(
        'ItemEntradaNF', on_delete=models.CASCADE, related_name='produtos_gerados',
    )
    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.PROTECT, related_name='+',
    )
    ordem = models.PositiveSmallIntegerField(default=1)
    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    unidade_estoque = models.CharField(max_length=10, blank=True)
    numero_lote = models.CharField(max_length=60, blank=True)
    data_validade = models.DateField(null=True, blank=True)
    custo_percentual = models.DecimalField(
        max_digits=7, decimal_places=4, null=True, blank=True,
        help_text='Percentual opcional do custo do item da NF aplicado a este produto.',
    )
    observacao = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'itens_entrada_nf_produtos_gerados'
        ordering = ['item', 'ordem', 'pk']
        indexes = [
            models.Index(fields=['item', 'ordem'], name='item_entrada_gerado_ord_idx'),
            models.Index(fields=['produto'], name='item_entrada_gerado_prod_idx'),
        ]
        verbose_name = 'Produto gerado da entrada'
        verbose_name_plural = 'Produtos gerados da entrada'

    def __str__(self):
        return f'{self.produto} (x{self.quantidade}) — item {self.item_id}'


class EntradaNFRateioFinanceiro(TimestampedModel):
    """Rateio financeiro da entrada por centro de custo / plano de contas."""

    entrada = models.ForeignKey(
        EntradaNF, on_delete=models.CASCADE, related_name='rateios_financeiros',
    )
    plano_contas = models.ForeignKey(
        'financeiro.PlanoContas', on_delete=models.PROTECT,
        related_name='rateios_entradas_nf',
    )
    centro_custo = models.ForeignKey(
        'financeiro.CentroCusto', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='rateios_entradas_nf',
    )
    percentual = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    observacao = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'entradas_nf_rateios_financeiros'
        ordering = ['entrada', 'pk']
        indexes = [
            models.Index(fields=['entrada'], name='entrada_rateio_fin_ent_idx'),
        ]
        verbose_name = 'Rateio financeiro da entrada'
        verbose_name_plural = 'Rateios financeiros da entrada'

    def __str__(self):
        return f'Rateio {self.percentual}% - {self.entrada}'


class EntradaNFAjusteFinanceiro(TimestampedModel):
    """Ajuste financeiro (acréscimo ou desconto) sobre o valor da entrada."""

    class Tipo(models.TextChoices):
        ACRESCIMO = 'acrescimo', 'Acrescimo'
        DESCONTO = 'desconto', 'Desconto'

    entrada = models.ForeignKey(
        EntradaNF, on_delete=models.CASCADE, related_name='ajustes_financeiros',
    )
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.ACRESCIMO)
    descricao = models.CharField(max_length=160)
    valor = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        db_table = 'entradas_nf_ajustes_financeiros'
        ordering = ['entrada', 'created_at', 'pk']
        indexes = [
            models.Index(fields=['entrada', 'tipo'], name='entrada_ajuste_fin_tipo_idx'),
        ]
        verbose_name = 'Ajuste financeiro da entrada'
        verbose_name_plural = 'Ajustes financeiros da entrada'

    @property
    def valor_assinado(self):
        """Desconto entra negativo; e' o que deixa a soma dos ajustes fechar."""
        sinal = Decimal('-1') if self.tipo == self.Tipo.DESCONTO else Decimal('1')
        return (self.valor or Decimal('0')) * sinal

    def __str__(self):
        return f'{self.get_tipo_display()} R${self.valor} - {self.entrada}'
