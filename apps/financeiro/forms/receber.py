"""Formulários de Contas a Receber."""
from datetime import date
from decimal import Decimal

from django import forms

from apps.cadastros.models import Cliente
from apps.financeiro.models.conta_bancaria import ContaBancaria, PlanoContas
from apps.financeiro.models.formas_pagamento import FormaPagamento

VALOR_WIDGET = forms.NumberInput(attrs={
    'step': '0.01',
    'inputmode': 'decimal',
    'data-decimal-places': '2',
})


class ContaReceberForm(forms.Form):
    """Lançamento manual de conta a receber."""

    cliente = forms.ModelChoiceField(
        queryset=Cliente.objects.none(),
        label='Cliente',
    )
    documento_numero = forms.CharField(
        max_length=20,
        required=False,
        label='Nº do documento',
        help_text='Número da NF, boleto ou outro documento de referência.',
    )
    parcela = forms.IntegerField(
        min_value=1,
        initial=1,
        label='Parcela',
        widget=forms.NumberInput(attrs={'min': '1'}),
    )
    total_parcelas = forms.IntegerField(
        min_value=1,
        initial=1,
        label='Total de parcelas',
        widget=forms.NumberInput(attrs={'min': '1'}),
    )
    valor_original = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0.01'),
        label='Valor (R$)',
        widget=VALOR_WIDGET,
    )
    data_emissao = forms.DateField(
        label='Data de emissão',
        widget=forms.DateInput(attrs={'type': 'date'}),
        initial=date.today,
    )
    data_vencimento = forms.DateField(
        label='Data de vencimento',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    forma_pagamento = forms.ModelChoiceField(
        queryset=FormaPagamento.objects.none(),
        required=False,
        label='Forma de pagamento',
    )
    plano_contas = forms.ModelChoiceField(
        queryset=PlanoContas.objects.none(),
        required=False,
        label='Categoria da receita',
        help_text='Selecione uma conta analítica do plano financeiro.',
    )
    observacao = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}),
        required=False,
        label='Observação',
    )

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial:
            self.fields['cliente'].queryset = (
                Cliente.objects.for_filial(filial)
                .filter(ativo=True)
                .order_by('razao_social')
            )
            self.fields['forma_pagamento'].queryset = (
                FormaPagamento.objects
                .filter(empresa=filial.empresa, ativo=True)
                .order_by('descricao')
            )
            self.fields['plano_contas'].queryset = (
                PlanoContas.objects
                .filter(
                    empresa=filial.empresa,
                    tipo='R',
                    ativo=True,
                    aceita_lancamento=True,
                )
                .order_by('codigo')
            )

    def clean(self):
        cleaned = super().clean()
        emissao = cleaned.get('data_emissao')
        vencimento = cleaned.get('data_vencimento')
        parcela = cleaned.get('parcela')
        total = cleaned.get('total_parcelas')
        if emissao and vencimento and vencimento < emissao:
            self.add_error('data_vencimento', 'Vencimento não pode ser anterior à emissão.')
        if parcela and total and parcela > total:
            self.add_error('parcela', 'Parcela não pode ser maior que o total de parcelas.')
        return cleaned


class BaixaContaReceberForm(forms.Form):
    """Registro de recebimento (baixa) de uma conta a receber."""

    data_pagamento = forms.DateField(
        label='Data do recebimento',
        widget=forms.DateInput(attrs={'type': 'date'}),
        initial=date.today,
    )
    valor_pago = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0.01'),
        label='Valor recebido (R$)',
        widget=VALOR_WIDGET,
    )
    valor_juros = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0'),
        required=False,
        initial=Decimal('0'),
        label='Juros (R$)',
        widget=VALOR_WIDGET,
    )
    valor_multa = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0'),
        required=False,
        initial=Decimal('0'),
        label='Multa (R$)',
        widget=VALOR_WIDGET,
    )
    valor_desconto = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0'),
        required=False,
        initial=Decimal('0'),
        label='Desconto (R$)',
        widget=VALOR_WIDGET,
    )
    forma_pagamento = forms.ModelChoiceField(
        queryset=FormaPagamento.objects.none(),
        label='Forma de recebimento',
    )
    conta_bancaria = forms.ModelChoiceField(
        queryset=ContaBancaria.objects.none(),
        required=False,
        label='Conta bancária',
        help_text='Conta onde o valor será creditado.',
    )
    observacao = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}),
        required=False,
        label='Observação',
    )

    def __init__(self, *args, filial=None, conta=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial:
            self.fields['forma_pagamento'].queryset = (
                FormaPagamento.objects
                .filter(empresa=filial.empresa, ativo=True)
                .order_by('descricao')
            )
            self.fields['conta_bancaria'].queryset = (
                ContaBancaria.objects.for_filial(filial)
                .filter(ativo=True)
                .order_by('descricao')
            )
        if conta:
            self.fields['valor_pago'].initial = conta.valor_saldo

    def clean(self):
        cleaned = super().clean()
        cleaned.setdefault('valor_juros', Decimal('0'))
        cleaned.setdefault('valor_multa', Decimal('0'))
        cleaned.setdefault('valor_desconto', Decimal('0'))
        return cleaned
