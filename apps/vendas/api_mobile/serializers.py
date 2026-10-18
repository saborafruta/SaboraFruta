from decimal import Decimal

from rest_framework import serializers

from apps.vendas.models import PedidoVenda


class ItemNovoPedidoSerializer(serializers.Serializer):
    produto_id = serializers.IntegerField(min_value=1)
    quantidade = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal('0.001'),
    )


class NovoPedidoSerializer(serializers.Serializer):
    cliente_id = serializers.IntegerField(min_value=1)
    forma_pagamento_id = serializers.IntegerField(min_value=1)
    condicao_pagamento_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    prazo_pagamento_dias = serializers.IntegerField(min_value=0, max_value=3650, required=False, default=0)
    prioridade = serializers.ChoiceField(
        choices=PedidoVenda.Prioridade.choices,
        required=False,
        default=PedidoVenda.Prioridade.NORMAL,
    )
    data_entrega_prevista = serializers.DateField(required=False, allow_null=True)
    observacao = serializers.CharField(required=False, allow_blank=True, max_length=2000, default='')
    observacao_interna = serializers.CharField(required=False, allow_blank=True, max_length=2000, default='')
    itens = ItemNovoPedidoSerializer(many=True, allow_empty=False)


class ItemCheckoutSerializer(serializers.Serializer):
    produto_id = serializers.IntegerField(min_value=1)
    quantidade = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal('0.001'))


class PagamentoCheckoutSerializer(serializers.Serializer):
    forma_id = serializers.IntegerField(min_value=1)
    valor = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal('0.01'))


class CheckoutVendaSerializer(serializers.Serializer):
    pendencia_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    cliente_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    desconto = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0'),
        required=False,
        default=Decimal('0'),
    )
    observacao = serializers.CharField(required=False, allow_blank=True, max_length=2000, default='')
    autorizacao_desconto = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
        default='',
    )
    itens = ItemCheckoutSerializer(many=True, allow_empty=False)
    pagamentos = PagamentoCheckoutSerializer(many=True, allow_empty=False)


class CheckoutPendenteSerializer(serializers.Serializer):
    cliente_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    desconto = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0'),
        required=False,
        default=Decimal('0'),
    )
    observacao = serializers.CharField(required=False, allow_blank=True, max_length=2000, default='')
    autorizacao_desconto = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
        default='',
    )
    itens = ItemCheckoutSerializer(many=True, allow_empty=False)
    pagamentos = PagamentoCheckoutSerializer(
        many=True,
        allow_empty=True,
        required=False,
        default=list,
    )


class AutorizacaoDescontoCheckoutSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    senha = serializers.CharField(max_length=128, trim_whitespace=False)


class ClienteRapidoCheckoutSerializer(serializers.Serializer):
    nome = serializers.CharField(max_length=150)
    cpf_cnpj = serializers.CharField(required=False, allow_blank=True, max_length=18, default='')
    celular = serializers.CharField(required=False, allow_blank=True, max_length=20, default='')

    def validate_cpf_cnpj(self, value):
        return ''.join(char for char in value if char.isdigit())


class ItemSeparacaoSerializer(serializers.Serializer):
    item_id = serializers.IntegerField(min_value=1)
    deposito_id = serializers.IntegerField(min_value=1)
    quantidade_separada = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal('0'),
    )
    conferido = serializers.BooleanField(default=True)
    observacao = serializers.CharField(required=False, allow_blank=True, max_length=255, default='')
    lotes = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list,
    )

    def validate_lotes(self, lotes):
        normalizados = []
        for lote in lotes:
            try:
                lote_id = int(lote.get('lote_id'))
                quantidade = Decimal(str(lote.get('quantidade')))
            except (TypeError, ValueError, ArithmeticError) as exc:
                raise serializers.ValidationError('Informe lote e quantidade válidos.') from exc
            if lote_id <= 0 or quantidade <= 0:
                raise serializers.ValidationError('Lote e quantidade devem ser positivos.')
            normalizados.append({'lote_id': lote_id, 'quantidade': quantidade})
        return normalizados


class ConcluirSeparacaoSerializer(serializers.Serializer):
    observacao = serializers.CharField(required=False, allow_blank=True, max_length=2000, default='')
    itens = ItemSeparacaoSerializer(many=True, allow_empty=False)


def validar_idempotency_key(request):
    chave = request.headers.get('Idempotency-Key', '').strip()
    if len(chave) < 8 or len(chave) > 64:
        raise serializers.ValidationError({
            'idempotency_key': 'Envie um Idempotency-Key entre 8 e 64 caracteres.',
        })
    return chave
