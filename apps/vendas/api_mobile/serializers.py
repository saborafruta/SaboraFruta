from decimal import Decimal

from rest_framework import serializers

from apps.cadastros.models import Cliente
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


class ClienteCompletoMobileSerializer(serializers.Serializer):
    tipo_pessoa = serializers.ChoiceField(choices=('F', 'J'))
    tipo = serializers.ChoiceField(choices=Cliente.Tipo.choices, default=Cliente.Tipo.VAREJO)
    razao_social = serializers.CharField(max_length=150)
    nome_fantasia = serializers.CharField(required=False, allow_blank=True, max_length=100, default='')
    cpf_cnpj = serializers.CharField(required=False, allow_blank=True, max_length=18, default='')
    rg_ie = serializers.CharField(required=False, allow_blank=True, max_length=20, default='')
    inscricao_municipal = serializers.CharField(required=False, allow_blank=True, max_length=20, default='')
    inscricao_estadual = serializers.CharField(required=False, allow_blank=True, max_length=20, default='')
    data_nascimento = serializers.DateField(required=False, allow_null=True, default=None)
    sexo = serializers.ChoiceField(choices=('', 'M', 'F', 'O'), required=False, allow_blank=True, default='')
    endereco = serializers.CharField(required=False, allow_blank=True, max_length=255, default='')
    numero = serializers.CharField(required=False, allow_blank=True, max_length=10, default='')
    complemento = serializers.CharField(required=False, allow_blank=True, max_length=60, default='')
    ponto_referencia = serializers.CharField(required=False, allow_blank=True, max_length=120, default='')
    bairro = serializers.CharField(required=False, allow_blank=True, max_length=80, default='')
    cidade = serializers.CharField(required=False, allow_blank=True, max_length=80, default='')
    uf = serializers.CharField(required=False, allow_blank=True, max_length=2, default='')
    cep = serializers.CharField(required=False, allow_blank=True, max_length=9, default='')
    codigo_municipio_ibge = serializers.CharField(required=False, allow_blank=True, max_length=7, default='')
    telefone = serializers.CharField(required=False, allow_blank=True, max_length=20, default='')
    celular = serializers.CharField(required=False, allow_blank=True, max_length=20, default='')
    email = serializers.EmailField(required=False, allow_blank=True, max_length=120, default='')
    email_nfe = serializers.EmailField(required=False, allow_blank=True, max_length=120, default='')
    contato_nome = serializers.CharField(required=False, allow_blank=True, max_length=100, default='')
    limite_credito = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal('0'), default=Decimal('0'))
    prazo_pagamento_dias = serializers.IntegerField(min_value=0, max_value=3650, default=0)
    consumidor_final = serializers.BooleanField(default=True)
    contribuinte_icms = serializers.BooleanField(default=False)
    optante_simples = serializers.BooleanField(default=False)
    bloqueado = serializers.BooleanField(default=False)
    motivo_bloqueio = serializers.CharField(required=False, allow_blank=True, max_length=2000, default='')
    observacao = serializers.CharField(required=False, allow_blank=True, max_length=4000, default='')

    def validate_cpf_cnpj(self, value):
        documento = ''.join(char for char in value if char.isdigit())
        if documento and len(documento) not in (11, 14):
            raise serializers.ValidationError('CPF deve ter 11 dígitos ou CNPJ deve ter 14.')
        return documento

    def validate_cep(self, value):
        cep = ''.join(char for char in value if char.isdigit())
        if cep and len(cep) != 8:
            raise serializers.ValidationError('CEP deve ter 8 dígitos.')
        return cep

    def validate_uf(self, value):
        return value.strip().upper()


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
