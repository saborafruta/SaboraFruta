"""Serializers da API de cadastro de produtos e apresentacoes.

`ProdutoSerializer` expoe um subconjunto de campos de `Produto` (o model
tem ~80 campos fiscais/logisticos/de granel) -- o essencial pra cadastro
via API. Campos fiscais detalhados continuam so no admin/formulario HTML
por enquanto; a API cresce sob demanda.
"""
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.produtos.models import ItemTabelaPreco, Produto, ProdutoApresentacao, UnidadeMedida


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

    @extend_schema_field(ProdutoApresentacaoLiteSerializer)
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


class TabelaPrecoLiteSerializer(serializers.Serializer):
    id = serializers.IntegerField(source='pk')
    descricao = serializers.CharField()
    tipo = serializers.CharField()


class ItemTabelaPrecoSerializer(serializers.ModelSerializer):
    tabela = TabelaPrecoLiteSerializer(read_only=True)
    # `valor_final` e' property do model (nao coluna) -- read_only explicito
    # obrigatorio, senao o ModelSerializer nao sabe de onde ele viria num write.
    valor_final = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = ItemTabelaPreco
        fields = [
            'id', 'tabela', 'preco_unitario', 'desconto_maximo', 'desconto_valor',
            'quantidade_minima', 'valor_final',
        ]


class FilialLiteSerializer(serializers.Serializer):
    id = serializers.IntegerField(source='pk')
    nome = serializers.SerializerMethodField()

    @extend_schema_field(str)
    def get_nome(self, obj):
        return obj.nome_fantasia or obj.razao_social


class DepositoLiteSerializer(serializers.Serializer):
    id = serializers.IntegerField(source='pk')
    nome = serializers.CharField()
    tipo = serializers.CharField()


class EstoqueSerializer(serializers.Serializer):
    # Serializer "solto" (o dado vem do model Estoque, mas nao e' um
    # ModelSerializer por causa dos dois campos aninhados) -- `ref_name`
    # evita colisao com apps.integracoes.serializers.EstoqueSerializer.
    class Meta:
        ref_name = 'EstoqueProdutoApi'

    id = serializers.IntegerField(source='pk')
    filial = FilialLiteSerializer()
    deposito = DepositoLiteSerializer()
    quantidade_atual = serializers.DecimalField(max_digits=12, decimal_places=3)
    quantidade_reservada = serializers.DecimalField(max_digits=12, decimal_places=3)
    quantidade_disponivel = serializers.DecimalField(max_digits=12, decimal_places=3)
    custo_medio = serializers.DecimalField(max_digits=14, decimal_places=4)
    ultima_entrada = serializers.DateTimeField(allow_null=True)
    ultima_saida = serializers.DateTimeField(allow_null=True)
    updated_at = serializers.DateTimeField()
