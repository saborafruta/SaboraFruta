"""Serializers da API de vendas do PDV.

`VendaCreateInputSerializer` valida so o envelope (itens/pagamentos como
listas de dict, e os campos soltos que `VendaPDVService.finalizar_venda`
aceita). Os dicts de cada item/pagamento NAO sao modelados campo a campo
aqui -- isso duplicaria o contrato inteiro de `VendaPDVService` (kits,
brindes, promocoes, oferta_contexto...) numa segunda fonte de verdade que
ia divergir com o tempo. A view passa os dicts quase direto pro service,
que ja valida cada campo e levanta `DadosInvalidosError` (formatado pelo
mesmo exception handler de `apps.produtos.api`).

O unico campo que EXISTE aqui e NAO existe no contrato original do
service e' `apresentacao_id`/`quantidade_comercial` por item -- ver
`apps/pdv/api/views.py` sobre a conversao pra unidade base antes de
chamar o service.
"""
from rest_framework import serializers

from apps.pdv.models import ItemVendaPDV, PagamentoVendaPDV, VendaPDV


class ItemVendaPDVSerializer(serializers.ModelSerializer):
    produto_descricao = serializers.CharField(source='produto.descricao', read_only=True)

    class Meta:
        model = ItemVendaPDV
        fields = [
            'id', 'numero_item', 'produto', 'produto_descricao', 'quantidade', 'unidade_medida',
            'valor_unitario', 'desconto_valor', 'valor_total', 'observacao',
        ]


class PagamentoVendaPDVSerializer(serializers.ModelSerializer):
    forma_pagamento_descricao = serializers.CharField(source='forma_pagamento.descricao', read_only=True)

    class Meta:
        model = PagamentoVendaPDV
        fields = ['id', 'forma_pagamento', 'forma_pagamento_descricao', 'valor', 'troco', 'numero_parcelas']


class VendaPDVSerializer(serializers.ModelSerializer):
    itens = ItemVendaPDVSerializer(many=True, read_only=True)
    pagamentos = PagamentoVendaPDVSerializer(many=True, read_only=True)

    class Meta:
        model = VendaPDV
        ref_name = 'VendaPDVApi'
        fields = [
            'id', 'numero_venda', 'status', 'cliente', 'observacao',
            'valor_subtotal', 'valor_desconto', 'valor_acrescimo', 'valor_total', 'valor_pago', 'troco',
            'bonificacao', 'data_venda', 'itens', 'pagamentos', 'created_at',
        ]


class ItemVendaInputSerializer(serializers.Serializer):
    """Um item do carrinho. `produto_id` e obrigatorio; o resto e' passthrough
    validado pelo proprio `VendaPDVService` (ver docstring do modulo)."""

    produto_id = serializers.IntegerField()
    quantidade = serializers.CharField(required=False)
    apresentacao_id = serializers.IntegerField(required=False, allow_null=True)
    quantidade_comercial = serializers.CharField(required=False, allow_null=True)

    def to_internal_value(self, data):
        # Preserva campos extras (preco_manual, desconto_valor, obs, kit_id,
        # oferta_tipo, brinde_id, ...) que o DRF descartaria por padrao --
        # ver docstring do modulo sobre nao remodelar o contrato inteiro aqui.
        validado = super().to_internal_value(data)
        extras = {k: v for k, v in data.items() if k not in self.fields}
        validado.update(extras)
        return validado


class PagamentoInputSerializer(serializers.Serializer):
    forma_id = serializers.IntegerField()
    valor = serializers.CharField()

    def to_internal_value(self, data):
        validado = super().to_internal_value(data)
        extras = {k: v for k, v in data.items() if k not in self.fields}
        validado.update(extras)
        return validado


class VendaCreateInputSerializer(serializers.Serializer):
    itens = ItemVendaInputSerializer(many=True)
    pagamentos = PagamentoInputSerializer(many=True, required=False, default=list)
    cliente_id = serializers.IntegerField(required=False, allow_null=True)
    desconto = serializers.CharField(required=False, default='0')
    acrescimo = serializers.CharField(required=False, default='0')
    observacao = serializers.CharField(required=False, allow_blank=True, default='')
    bonificacao = serializers.BooleanField(required=False, default=False)
    # Default True: mesmo comportamento do fluxo HTML existente do PDV
    # (`api_venda_finalizar`, que hardcoda True) -- venda com item sem
    # saldo suficiente e' registrada mesmo assim (so' nao baixa aquele
    # item). Passar False faz a API rejeitar a venda inteira nesse caso.
    forcar_estoque_negativo = serializers.BooleanField(required=False, default=True)

    def validate_itens(self, valor):
        if not valor:
            raise serializers.ValidationError('Carrinho vazio.')
        return valor
