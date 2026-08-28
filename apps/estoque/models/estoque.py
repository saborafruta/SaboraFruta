"""
Estoque consolidado por produto×filial + MovimentacaoEstoque (auditoria completa).

IMPORTANTE: toda escrita passa obrigatoriamente por MovimentacaoService.
Nunca incrementar/decrementar Estoque.quantidade_atual diretamente.
"""
from django.db import models

from apps.core.models.base import FilialScopedModel


class Estoque(models.Model):
    """
    Estoque consolidado por produto×filial.
    quantidade_disponivel = quantidade_atual - quantidade_reservada
    """

    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.PROTECT, related_name='estoques',
    )
    filial = models.ForeignKey(
        'core.Filial', on_delete=models.PROTECT, related_name='estoques',
    )
    quantidade_atual = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    quantidade_reservada = models.DecimalField(
        max_digits=12, decimal_places=3, default=0,
        help_text='Pedidos confirmados ainda não faturados',
    )
    quantidade_disponivel = models.DecimalField(
        max_digits=12, decimal_places=3, default=0,
        help_text='atual - reservada. Mantido pelo service.',
    )
    custo_medio = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    ultima_entrada = models.DateTimeField(null=True, blank=True)
    ultima_saida = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'estoque'
        unique_together = [('produto', 'filial')]
        indexes = [
            models.Index(fields=['filial', 'quantidade_disponivel']),
        ]
        verbose_name = 'Estoque'
        verbose_name_plural = 'Estoques'

    def __str__(self):
        return f'{self.produto} @ {self.filial}: {self.quantidade_disponivel}'

    def atualizar_disponivel(self):
        """Recalcula quantidade_disponivel. Chamar após alterar atual ou reservada."""
        self.quantidade_disponivel = self.quantidade_atual - self.quantidade_reservada


class MovimentacaoEstoque(FilialScopedModel):
    """
    Registro auditável de toda movimentação de estoque.
    Mantém snapshots de quantidade ANTES e DEPOIS para auditoria forense.
    """

    class TipoOperacao(models.TextChoices):
        ENTRADA = 'entrada', 'Entrada'
        SAIDA = 'saida', 'Saída'
        TRANSFERENCIA_SAIDA = 'transferencia_saida', 'Transferência (saída)'
        TRANSFERENCIA_ENTRADA = 'transferencia_entrada', 'Transferência (entrada)'
        AJUSTE_MAIS = 'ajuste_mais', 'Ajuste +'
        AJUSTE_MENOS = 'ajuste_menos', 'Ajuste -'
        INVENTARIO = 'inventario', 'Inventário'
        DEVOLUCAO_CLIENTE = 'devolucao_cliente', 'Devolução de Cliente'
        DEVOLUCAO_FORNECEDOR = 'devolucao_fornecedor', 'Devolução ao Fornecedor'
        BONIFICACAO = 'bonificacao', 'Bonificação'
        ROUBO = 'roubo', 'Roubo/Furto'
        PERDA = 'perda', 'Perda'
        DETERIORACAO = 'deterioracao', 'Deterioração'
        BAIXA_VALIDADE = 'baixa_validade', 'Baixa por Validade'
        USO_PROPRIO = 'uso_proprio', 'Uso Próprio'
        BRINDE = 'brinde', 'Brinde'
        QUEBRA = 'quebra', 'Quebra'
        PRODUCAO_ENTRADA = 'producao_entrada', 'Produção (entrada)'
        PRODUCAO_SAIDA = 'producao_saida', 'Produção (saída MP)'

    class DocumentoTipo(models.TextChoices):
        PEDIDO_VENDA = 'pedido_venda', 'Pedido de Venda'
        NFE = 'nfe', 'NF-e'
        NFCE = 'nfce', 'NFC-e'
        OUTRAS = 'outras_movimentacoes', 'Outras Movimentações'
        INVENTARIO = 'inventario', 'Inventário'
        TRANSFERENCIA = 'transferencia', 'Transferência'
        AJUSTE_MANUAL = 'ajuste_manual', 'Ajuste Manual'
        ORDEM_PRODUCAO = 'ordem_producao', 'Ordem de Produção'
        ESTORNO_ENTRADA = 'estorno_entrada', 'Estorno de Entrada'
        COMANDA = 'comanda', 'Comanda (Food Service)'

    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.PROTECT, related_name='movimentacoes',
    )
    lote = models.ForeignKey(
        'estoque.LoteProduto', on_delete=models.PROTECT, null=True, blank=True,
        related_name='movimentacoes',
    )
    tipo_operacao = models.CharField(max_length=40, choices=TipoOperacao.choices, db_index=True)
    documento_tipo = models.CharField(max_length=30, choices=DocumentoTipo.choices, blank=True)
    documento_id = models.BigIntegerField(null=True, blank=True)
    documento_numero = models.CharField(max_length=20, blank=True)
    documento_fiscal = models.ForeignKey(
        'financeiro.DocumentoFiscal', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='movimentacoes_estoque',
        help_text='NF-e vinculada (ex.: transferência entre lojas).',
    )

    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    quantidade_anterior = models.DecimalField(
        max_digits=12, decimal_places=3,
        help_text='Snapshot ANTES',
    )
    quantidade_posterior = models.DecimalField(
        max_digits=12, decimal_places=3,
        help_text='Snapshot APÓS',
    )
    valor_unitario = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True,
    )
    valor_total = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
    )
    custo_medio_anterior = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True,
    )
    custo_medio_posterior = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True,
    )

    # Granel
    peso_bruto = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    tara = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    peso_liquido = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)

    # Rastreio
    usuario = models.ForeignKey(
        'core.Usuario', on_delete=models.PROTECT, related_name='movimentacoes',
    )
    filial_destino = models.ForeignKey(
        'core.Filial', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
        help_text='Para transferências',
    )
    # PARA QUEM A MERCADORIA SAIU, quando a saída tem um destinatário.
    #
    # O razão sabia o QUE saiu, QUANDO e POR QUAL documento — não sabia PARA
    # QUEM. Na venda isso se descobre pelo pedido; na bonificação não havia
    # como: uma viagem que entrega cortesia a dois clientes gerava dois
    # movimentos indistinguíveis, e "para quem demos as 20 caixas?" só se
    # respondia por conferência manual.
    cliente = models.ForeignKey(
        'cadastros.Cliente', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='movimentacoes_estoque',
        help_text='Destinatário da saída, quando existe.',
    )
    observacao = models.TextField(blank=True)
    data_movimentacao = models.DateTimeField(db_index=True)

    # Cancelamento (estorno de transferências)
    transferencia_cancelada = models.BooleanField(default=False, db_index=True)
    transferencia_cancelada_em = models.DateTimeField(null=True, blank=True)
    transferencia_cancelada_por = models.ForeignKey(
        'core.Usuario', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )

    class Meta:
        db_table = 'movimentacoes_estoque'
        ordering = ['-data_movimentacao']
        indexes = [
            models.Index(fields=['produto', '-data_movimentacao']),
            models.Index(fields=['filial', 'tipo_operacao']),
            models.Index(fields=['documento_tipo', 'documento_id']),
        ]
        verbose_name = 'Movimentação de Estoque'
        verbose_name_plural = 'Movimentações de Estoque'
