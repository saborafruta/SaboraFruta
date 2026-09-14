"""Serializers da API de equalização -- a maioria envolve dicts que os services já devolvem, não models, então são `Serializer` simples, não `ModelSerializer`."""
from decimal import Decimal

from rest_framework import serializers

from apps.estoque.models import SolicitacaoTransferencia, SugestaoEqualizacaoSnapshot

QUANTIDADE_MINIMA = Decimal("0.001")


class FilialLiteSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="pk")
    nome = serializers.SerializerMethodField()

    def get_nome(self, obj):
        return obj.nome_fantasia or obj.razao_social


class ProdutoLiteSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="pk")
    descricao = serializers.CharField()
    codigo = serializers.CharField()


class SugestaoSerializer(serializers.Serializer):
    """Espelha o dict de `equilibrio_estoque.calcular_equilibrio`."""

    produto = ProdutoLiteSerializer()
    origem = FilialLiteSerializer()
    destino = FilialLiteSerializer()
    quantidade = serializers.DecimalField(max_digits=12, decimal_places=3)
    origem_saldo = serializers.DecimalField(max_digits=12, decimal_places=3)
    destino_saldo = serializers.DecimalField(max_digits=12, decimal_places=3)
    origem_cobertura = serializers.DecimalField(max_digits=6, decimal_places=1, allow_null=True)
    destino_cobertura = serializers.DecimalField(max_digits=6, decimal_places=1, allow_null=True)
    destino_classe = serializers.CharField()
    destino_classe_label = serializers.CharField()
    destino_meta = serializers.DecimalField(max_digits=12, decimal_places=3)
    destino_a_caminho = serializers.DecimalField(max_digits=12, decimal_places=3)
    produto_parado_origem = serializers.BooleanField()
    destino_sem_estoque = serializers.BooleanField()
    destino_pedido_pendente = serializers.BooleanField()
    alerta_fefo = serializers.CharField(allow_blank=True)
    score = serializers.IntegerField()


class AnaliseFiltroSerializer(serializers.Serializer):
    """Filtros comuns a /recomendacoes/ e /analisar/."""

    dias_analise = serializers.ChoiceField(choices=[7, 15, 30, 60, 90], default=30)
    dias_cobertura = serializers.ChoiceField(choices=[7, 14, 21, 30, 45], default=14)
    busca = serializers.CharField(required=False, allow_blank=True, default="")
    origem = serializers.IntegerField(required=False, allow_null=True, default=None)
    destino = serializers.IntegerField(required=False, allow_null=True, default=None)


class DashboardSerializer(serializers.Serializer):
    """Espelha `dashboard_equalizacao.montar_dashboard`."""

    estoque_total = serializers.DecimalField(max_digits=14, decimal_places=2)
    estoque_excedente = serializers.DecimalField(max_digits=14, decimal_places=2)
    estoque_critico = serializers.DecimalField(max_digits=14, decimal_places=2)
    produtos_ruptura = serializers.IntegerField()
    produtos_criticos = serializers.IntegerField()
    produtos_parados = serializers.IntegerField()
    transferencias_sugeridas = serializers.IntegerField()
    transferencias_aprovadas = serializers.IntegerField()
    transferencias_em_transito = serializers.IntegerField()
    valor_recuperavel = serializers.DecimalField(max_digits=14, decimal_places=2)
    capital_parado = serializers.DecimalField(max_digits=14, decimal_places=2)
    cobertura_media = serializers.DecimalField(max_digits=6, decimal_places=1, allow_null=True)


class IndicadoresAtuaisSerializer(serializers.Serializer):
    taxa_ruptura_pct = serializers.DecimalField(max_digits=5, decimal_places=1, allow_null=True)
    cobertura_media_dias = serializers.DecimalField(max_digits=6, decimal_places=1, allow_null=True)
    giro_estoque = serializers.DecimalField(max_digits=8, decimal_places=2, allow_null=True)
    capital_parado = serializers.DecimalField(max_digits=14, decimal_places=2)
    pct_estoque_excedente = serializers.DecimalField(max_digits=5, decimal_places=1, allow_null=True)
    pct_estoque_abaixo_minimo = serializers.DecimalField(max_digits=5, decimal_places=1, allow_null=True)


class IndicadoresHistoricoSerializer(serializers.Serializer):
    dias_historico = serializers.IntegerField()
    transferencias_realizadas = serializers.IntegerField()
    valor_redistribuido = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_sugestoes_geradas = serializers.IntegerField()
    transferencias_nao_aplicadas = serializers.IntegerField(allow_null=True)
    acuracidade_recomendacao_pct = serializers.DecimalField(max_digits=5, decimal_places=1, allow_null=True)


class IndicadoresSerializer(serializers.Serializer):
    atuais = IndicadoresAtuaisSerializer()
    historico = IndicadoresHistoricoSerializer()


class SimularTransferenciaInputSerializer(serializers.Serializer):
    produto_id = serializers.IntegerField()
    origem_id = serializers.IntegerField()
    destino_id = serializers.IntegerField()
    quantidade = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=QUANTIDADE_MINIMA)
    dias_analise = serializers.ChoiceField(choices=[7, 15, 30, 60, 90], default=30)
    dias_cobertura = serializers.ChoiceField(choices=[7, 14, 21, 30, 45], default=14)


class SimulacaoResultadoSerializer(serializers.Serializer):
    produto = ProdutoLiteSerializer()
    origem = FilialLiteSerializer()
    destino = FilialLiteSerializer()
    quantidade = serializers.DecimalField(max_digits=12, decimal_places=3)
    excede_saldo_origem = serializers.BooleanField()
    origem_saldo_antes = serializers.DecimalField(max_digits=12, decimal_places=3)
    origem_cobertura_antes = serializers.DecimalField(max_digits=6, decimal_places=1, allow_null=True)
    destino_saldo_antes = serializers.DecimalField(max_digits=12, decimal_places=3)
    destino_cobertura_antes = serializers.DecimalField(max_digits=6, decimal_places=1, allow_null=True)
    destino_classe_antes = serializers.CharField()
    origem_saldo_depois = serializers.DecimalField(max_digits=12, decimal_places=3)
    origem_cobertura_depois = serializers.DecimalField(max_digits=6, decimal_places=1, allow_null=True)
    destino_saldo_depois = serializers.DecimalField(max_digits=12, decimal_places=3)
    destino_cobertura_depois = serializers.DecimalField(max_digits=6, decimal_places=1, allow_null=True)
    destino_classe_depois_label = serializers.CharField()
    reducao_excesso_dias = serializers.DecimalField(max_digits=6, decimal_places=1, allow_null=True)
    reduz_risco_ruptura = serializers.BooleanField()
    capital_redistribuido = serializers.DecimalField(max_digits=14, decimal_places=2)


class TransferirInputSerializer(serializers.Serializer):
    produto_id = serializers.IntegerField()
    destino_id = serializers.IntegerField()
    quantidade = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=QUANTIDADE_MINIMA)
    motivo = serializers.CharField(required=False, allow_blank=True, default="")


class SolicitacaoTransferenciaSerializer(serializers.ModelSerializer):
    produto_descricao = serializers.CharField(source="produto.descricao", read_only=True)
    filial_origem_nome = serializers.SerializerMethodField()
    filial_destino_nome = serializers.SerializerMethodField()
    solicitante_nome = serializers.CharField(source="solicitante.nome", read_only=True)
    aprovador_nome = serializers.CharField(source="aprovador.nome", read_only=True, allow_null=True)

    class Meta:
        model = SolicitacaoTransferencia
        fields = [
            "id", "produto", "produto_descricao", "filial_origem", "filial_origem_nome",
            "filial_destino", "filial_destino_nome", "quantidade", "valor_estimado", "motivo",
            "status", "solicitante", "solicitante_nome", "aprovador", "aprovador_nome",
            "decidida_em", "observacao_decisao", "documento_numero", "created_at",
        ]
        read_only_fields = fields

    def get_filial_origem_nome(self, obj):
        return obj.filial_origem.nome_fantasia or obj.filial_origem.razao_social

    def get_filial_destino_nome(self, obj):
        return obj.filial_destino.nome_fantasia or obj.filial_destino.razao_social


class AprovarRejeitarInputSerializer(serializers.Serializer):
    solicitacao_id = serializers.IntegerField()
    observacao = serializers.CharField(required=False, allow_blank=True, default="")


class SnapshotHistoricoSerializer(serializers.ModelSerializer):
    produto_descricao = serializers.CharField(source="produto.descricao", read_only=True)
    filial_origem_nome = serializers.SerializerMethodField()
    filial_destino_nome = serializers.SerializerMethodField()

    class Meta:
        model = SugestaoEqualizacaoSnapshot
        fields = [
            "id", "produto", "produto_descricao", "filial_origem", "filial_origem_nome",
            "filial_destino", "filial_destino_nome", "quantidade_sugerida", "score",
            "destino_meta", "created_at",
        ]
        read_only_fields = fields

    def get_filial_origem_nome(self, obj):
        return obj.filial_origem.nome_fantasia or obj.filial_origem.razao_social

    def get_filial_destino_nome(self, obj):
        return obj.filial_destino.nome_fantasia or obj.filial_destino.razao_social
