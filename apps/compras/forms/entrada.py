from decimal import Decimal

from django import forms

from apps.cadastros.models import Fornecedor
from apps.compras.models import EntradaNF, EntradaNFAjusteFinanceiro, EntradaNFParcela, PedidoCompra
from apps.estoque.models import Deposito
from apps.produtos.models import Produto


class EntradaNFForm(forms.ModelForm):
    class Meta:
        model = EntradaNF
        fields = [
            'pedido_compra', 'fornecedor', 'numero_nf', 'serie_nf',
            'chave_acesso_nf', 'data_emissao_nf', 'tipo',
            'tipo_entrada_operacional', 'origem_fiscal',
            'movimenta_estoque', 'movimenta_financeiro', 'altera_custo_estoque',
            'deposito',
            'valor_frete', 'valor_seguro', 'valor_outras_despesas',
            'observacao',
        ]
        widgets = {
            'data_emissao_nf': forms.DateInput(attrs={'type': 'date'}),
            'observacao': forms.Textarea(attrs={'rows': 2}),
            'chave_acesso_nf': forms.TextInput(attrs={'maxlength': '44'}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial:
            self.fields['fornecedor'].queryset = Fornecedor.objects.for_filial(filial).filter(
                ativo=True,
            ).order_by('razao_social')
            self.fields['fornecedor'].required = False
            self.fields['fornecedor'].help_text = (
                'Se não informar, a entrada usa Fornecedor não cadastrado temporariamente.'
            )
            self.fields['pedido_compra'].queryset = PedidoCompra.objects.for_filial(filial).filter(
                status__in=[
                    PedidoCompra.Status.APROVADO,
                    PedidoCompra.Status.ENVIADO_FORNECEDOR,
                    PedidoCompra.Status.CONFIRMADO_FORNECEDOR,
                    PedidoCompra.Status.PARCIALMENTE_RECEBIDO,
                ],
            ).order_by('-data_emissao')
            self.fields['pedido_compra'].required = False
            self.fields['deposito'].queryset = Deposito.objects.filter(
                filial=filial, ativo=True,
            ).order_by('-is_padrao', 'nome')
            self.fields['deposito'].required = False
            self.fields['deposito'].help_text = (
                'Onde a mercadoria entra. Vazio = depósito padrão da filial.'
            )

    def clean_chave_acesso_nf(self):
        chave = ''.join(filter(str.isdigit, self.cleaned_data.get('chave_acesso_nf', '') or ''))
        if chave and len(chave) != 44:
            raise forms.ValidationError('Chave de acesso deve ter 44 digitos.')
        return chave

    def clean(self):
        cleaned = super().clean()
        for campo in ('movimenta_estoque', 'movimenta_financeiro', 'altera_custo_estoque'):
            cleaned[campo] = bool(cleaned.get(campo))
        return cleaned


class ImportarXMLForm(forms.Form):
    tipo_entrada_operacional = forms.ChoiceField(
        label='Tipo de entrada',
        choices=EntradaNF.TipoEntradaOperacional.choices,
        initial=EntradaNF.TipoEntradaOperacional.COMPRA_REVENDA,
        required=False,
    )
    origem_fiscal = forms.ChoiceField(
        label='Origem',
        choices=EntradaNF.OrigemFiscal.choices,
        initial=EntradaNF.OrigemFiscal.NACIONAL,
        required=False,
    )
    movimenta_estoque = forms.BooleanField(label='Estoque', required=False, initial=True)
    movimenta_financeiro = forms.BooleanField(label='Financeiro', required=False, initial=True)
    altera_custo_estoque = forms.BooleanField(label='Alterar custo', required=False, initial=True)
    arquivo_xml = forms.FileField(
        label='Arquivo XML da NF-e',
        help_text='Envie o XML completo recebido do fornecedor.',
    )

    def clean(self):
        cleaned = super().clean()
        tipo = cleaned.get('tipo_entrada_operacional')
        if tipo not in dict(EntradaNF.TipoEntradaOperacional.choices):
            tipo = EntradaNF.TipoEntradaOperacional.COMPRA_REVENDA
            cleaned['tipo_entrada_operacional'] = tipo
        if cleaned.get('origem_fiscal') not in dict(EntradaNF.OrigemFiscal.choices):
            cleaned['origem_fiscal'] = EntradaNF.OrigemFiscal.NACIONAL
        for campo in ('movimenta_estoque', 'movimenta_financeiro', 'altera_custo_estoque'):
            cleaned[campo] = bool(cleaned.get(campo))
        return cleaned

    def clean_arquivo_xml(self):
        arquivo = self.cleaned_data['arquivo_xml']
        nome = arquivo.name.lower()
        if not nome.endswith('.xml'):
            raise forms.ValidationError('Envie um arquivo .xml.')
        if arquivo.size > 5 * 1024 * 1024:
            raise forms.ValidationError('XML muito grande. Limite de 5 MB.')
        return arquivo


class ConsultarChaveForm(forms.Form):
    chave_acesso = forms.CharField(
        label='Chave de acesso',
        max_length=44,
        widget=forms.TextInput(attrs={
            'maxlength': '44',
            'inputmode': 'numeric',
            'autocomplete': 'off',
        }),
    )

    def clean_chave_acesso(self):
        chave = ''.join(filter(str.isdigit, self.cleaned_data.get('chave_acesso', '') or ''))
        if len(chave) != 44:
            raise forms.ValidationError('Chave de acesso deve ter 44 digitos.')
        return chave


class EntradaNFParcelaForm(forms.ModelForm):
    class Meta:
        model = EntradaNFParcela
        fields = ['numero', 'data_vencimento', 'valor', 'forma_pagamento', 'observacao']
        widgets = {
            'data_vencimento': forms.DateInput(attrs={'type': 'date'}),
            'observacao': forms.TextInput(),
        }
        labels = {
            'numero': 'Número/parcela',
            'data_vencimento': 'Vencimento',
            'valor': 'Valor',
            'forma_pagamento': 'Forma de pagamento',
            'observacao': 'Observação',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['numero'].required = False
        self.fields['data_vencimento'].required = True
        self.fields['valor'].min_value = Decimal('0.01')
        self.fields['forma_pagamento'].required = False
        self.fields['observacao'].required = False


class EntradaNFAjusteFinanceiroForm(forms.ModelForm):
    class Meta:
        model = EntradaNFAjusteFinanceiro
        fields = ['tipo', 'descricao', 'valor']
        labels = {
            'tipo': 'Tipo',
            'descricao': 'Descrição',
            'valor': 'Valor',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['valor'].min_value = Decimal('0.01')


class AdicionarItemEntradaForm(forms.Form):
    produto = forms.ModelChoiceField(queryset=Produto.objects.none(), label='Produto')
    ean_xml = forms.CharField(max_length=32, required=False, label='EAN da nota')
    codigo_produto_fornecedor = forms.CharField(
        max_length=80, required=False, label='Código fornecedor',
    )
    descricao_xml = forms.CharField(max_length=255, required=False, label='Descrição da nota')
    quantidade = forms.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal('0.001'))
    unidade_xml = forms.CharField(max_length=10, required=False, initial='UN', label='Unidade nota')
    fator_conversao = forms.DecimalField(
        max_digits=12,
        decimal_places=4,
        min_value=Decimal('0.0001'),
        initial=Decimal('1'),
        label='Fator',
        help_text='Ex: 10 CX x fator 12 = 120 UN no estoque.',
    )
    quantidade_recebida = forms.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal('0.001'),
        required=False,
        label='Qtd recebida',
    )
    valor_unitario = forms.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=0,
        initial=0,
        required=False,
    )
    valor_ipi = forms.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, initial=0, required=False,
        label='IPI (R$)',
    )
    valor_icms = forms.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, initial=0, required=False,
        label='ICMS (R$)',
    )
    numero_lote = forms.CharField(max_length=60, required=False, label='Número do lote')
    data_fabricacao = forms.DateField(
        required=False, widget=forms.DateInput(attrs={'type': 'date'}),
    )
    data_validade = forms.DateField(
        required=False, widget=forms.DateInput(attrs={'type': 'date'}),
    )

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial:
            self.fields['produto'].queryset = Produto.objects.for_filial(filial).filter(
                ativo=True,
            ).order_by('descricao')

    def clean(self):
        cleaned = super().clean()
        produto = cleaned.get('produto')
        fator = cleaned.get('fator_conversao') or Decimal('1')
        quantidade = cleaned.get('quantidade') or Decimal('0')
        quantidade_recebida = cleaned.get('quantidade_recebida')
        if cleaned.get('valor_unitario') is None:
            cleaned['valor_unitario'] = Decimal('0')
        if quantidade_recebida is None and quantidade:
            cleaned['quantidade_recebida'] = quantidade * fator
        if produto:
            if produto.controla_lote and not cleaned.get('numero_lote'):
                raise forms.ValidationError(f'Produto "{produto}" requer número de lote.')
            if produto.controla_validade and not cleaned.get('data_validade'):
                raise forms.ValidationError(f'Produto "{produto}" requer data de validade.')
        return cleaned
