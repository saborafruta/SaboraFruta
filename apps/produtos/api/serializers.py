"""Serializers da API de cadastro de produtos e apresentacoes.

`ProdutoSerializer` expoe um subconjunto de campos de `Produto` (o model
tem ~80 campos fiscais/logisticos/de granel) -- o essencial pra cadastro
via API. Campos fiscais detalhados continuam so no admin/formulario HTML
por enquanto; a API cresce sob demanda.
"""
from rest_framework import serializers

from apps.produtos.models import Produto, ProdutoApresentacao, UnidadeMedida


class UnidadeMedidaLiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = UnidadeMedida
        fields = ['id', 'sigla', 'descricao', 'tipo', 'casas_decimais']


class ProdutoApresentacaoSerializer(serializers.ModelSerializer):
    unidade_detalhe = UnidadeMedidaLiteSerializer(source='unidade', read_only=True)
    cubagem = serializers.DecimalField(max_digits=14, decimal_places=6, read_only=True)

    class Meta:
        model = ProdutoApresentacao
        fields = [
            'id', 'produto', 'unidade', 'unidade_detalhe', 'descricao', 'codigo',
            'fator_conversao', 'preco_venda', 'preco_minimo',
            'peso_bruto', 'peso_liquido', 'largura', 'altura', 'profundidade', 'cubagem',
            'permite_venda', 'permite_compra', 'permite_estoque',
            'principal_venda', 'principal_compra', 'ativo',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'produto', 'created_at', 'updated_at']

    def validate_fator_conversao(self, valor):
        if valor is not None and valor <= 0:
            raise serializers.ValidationError('O fator de conversao deve ser maior que zero.')
        return valor


class ProdutoApresentacaoLiteSerializer(serializers.ModelSerializer):
    """Usado em listagens onde o item completo de apresentacao seria demais."""

    unidade_sigla = serializers.CharField(source='unidade.sigla', read_only=True)

    class Meta:
        model = ProdutoApresentacao
        fields = ['id', 'descricao', 'unidade_sigla', 'fator_conversao', 'preco_venda', 'ativo']


class ProdutoSerializer(serializers.ModelSerializer):
    apresentacao_principal_venda = serializers.SerializerMethodField()

    class Meta:
        model = Produto
        # `ref_name` evita colisao no componente OpenAPI com
        # apps.integracoes.serializers.ProdutoSerializer (nome igual, schema
        # diferente -- sem isso o drf-spectacular mistura os dois no doc).
        ref_name = 'ProdutoCadastro'
        fields = [
            'id', 'codigo', 'descricao', 'descricao_curta',
            'categoria', 'subcategoria', 'marca', 'fornecedor',
            'unidade_medida', 'tipo_produto', 'ncm',
            'preco_custo', 'preco_venda', 'ativo',
            'controla_lote', 'controla_validade', 'permite_venda_sem_estoque',
            'apresentacao_principal_venda',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_apresentacao_principal_venda(self, obj):
        # A view faz `.prefetch_related('apresentacoes')`, entao filtrar em
        # Python aqui nao dispara outra query por produto na listagem.
        apresentacao = next(
            (a for a in obj.apresentacoes.all() if a.principal_venda and a.ativo),
            None,
        )
        if apresentacao is None:
            return None
        return ProdutoApresentacaoLiteSerializer(apresentacao).data


class ProdutoDetalheSerializer(ProdutoSerializer):
    apresentacoes = ProdutoApresentacaoLiteSerializer(many=True, read_only=True)

    class Meta(ProdutoSerializer.Meta):
        ref_name = 'ProdutoCadastroDetalhe'
        fields = ProdutoSerializer.Meta.fields + ['apresentacoes']
