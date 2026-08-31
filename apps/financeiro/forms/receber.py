"""Formulários de Contas a Receber."""
from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.financeiro.models.conta_bancaria import ContaBancaria, PlanoContas
from apps.financeiro.models.formas_pagamento import FormaPagamento
from apps.financeiro.forms.plano_contas import CategoriaFinanceiraChoiceField
from apps.financeiro.forms.cartao import campo_parcelas, configurar_forma_pagamento, limpar_dados_cartao
from apps.financeiro.models.receber_pagar import ContaReceber
from apps.financeiro.services.entrega_receber import entrega_receber_habilitada, validar_entrega_receber
from apps.core.services.exceptions import DomainError

VALOR_WIDGET = forms.NumberInput(attrs={
    'step': '0.01',
    'inputmode': 'decimal',
    'data-decimal-places': '2',
})

BANDEIRAS_CARTAO = [
    ('', 'Não informar'),
    ('visa', 'Visa'),
    ('mastercard', 'Mastercard'),
    ('elo', 'Elo'),
    ('amex', 'Amex'),
    ('hiper', 'Hiper / Hipercard'),
]


class ReferenciaContaReceberForm(forms.Form):
    """Documento e entrega, sem permitir alterações financeiras."""
    documento_numero = forms.CharField(
        max_length=200,
        required=False,
        label='Documento',
        help_text='Número ou descrição do pedido, NF ou boleto. Até 200 caracteres.',
    )
    status_entrega = forms.ChoiceField(
        choices=ContaReceber.StatusEntrega.choices,
        initial=ContaReceber.StatusEntrega.SEM_PREVISAO,
        label='Situação da entrega', required=False,
    )
    data_entrega_prevista = forms.DateField(
        required=False, label='Data prevista de entrega',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
    )
    previsao_entrega_complemento = forms.CharField(
        max_length=100, required=False, label='Complemento da previsão',
        help_text='Ex.: Outubro/2026, quando ainda não houver dia definido.',
    )

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.entrega_habilitada = entrega_receber_habilitada(filial)
        if not self.entrega_habilitada:
            for campo in ('status_entrega', 'data_entrega_prevista', 'previsao_entrega_complemento'):
                self.fields.pop(campo)

    def clean(self):
        cleaned = super().clean()
        if self.entrega_habilitada:
            status = cleaned.get('status_entrega') or ContaReceber.StatusEntrega.SEM_PREVISAO
            cleaned['status_entrega'] = status
            try:
                validar_entrega_receber(status, cleaned.get('data_entrega_prevista'),
                                       cleaned.get('previsao_entrega_complemento', ''))
            except DomainError as exc:
                raise forms.ValidationError(str(exc)) from exc
        return cleaned


class ContaReceberForm(ReferenciaContaReceberForm):
    """Lançamento manual de conta a receber."""

    cliente = forms.ModelChoiceField(
        queryset=Cliente.objects.none(),
        label='Cliente',
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
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
        initial=timezone.localdate,
    )
    data_vencimento = forms.DateField(
        label='Data de vencimento',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
    )
    forma_pagamento = forms.ModelChoiceField(
        queryset=FormaPagamento.objects.none(),
        required=False,
        label='Forma de pagamento',
    )
    plano_contas = CategoriaFinanceiraChoiceField(
        queryset=PlanoContas.objects.none(),
        required=False,
        label='Categoria financeira',
        help_text='Grupo > Subgrupo > Categoria. A conta contábil será preenchida automaticamente.',
    )
    observacao = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}),
        required=False,
        label='Observação',
    )
    quitar_ao_lancar = forms.BooleanField(
        required=False,
        initial=False,
        label='Lançar e receber agora',
    )
    modo_baixa = forms.ChoiceField(
        choices=[
            ('integral', 'Receber tudo'),
            ('parcial', 'Parcial'),
            ('metade', '50%'),
        ],
        required=False,
        initial='integral',
        label='Tipo de recebimento',
    )
    data_pagamento_inicial = forms.DateField(
        label='Data do recebimento',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
        initial=timezone.localdate,
        required=False,
    )
    valor_pago_inicial = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0.01'),
        required=False,
        label='Valor recebido (R$)',
        widget=VALOR_WIDGET,
    )
    forma_pagamento_utilizada = forms.ModelChoiceField(
        queryset=FormaPagamento.objects.none(),
        required=False,
        label='Forma de recebimento',
    )
    conta_bancaria_recebimento = forms.ModelChoiceField(
        queryset=ContaBancaria.objects.none(),
        required=False,
        label='Conta creditada',
    )

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, filial=filial, **kwargs)
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
            configurar_forma_pagamento(self, (
                FormaPagamento.objects
                .filter(empresa=filial.empresa, ativo=True)
                .order_by('descricao')
            ), field_name='forma_pagamento_utilizada')
            self.fields['conta_bancaria_recebimento'].queryset = (
                ContaBancaria.objects.for_filial(filial)
                .filter(ativo=True)
                .order_by('descricao')
            )
            categorias = (
                PlanoContas.objects
                .filter(
                    empresa=filial.empresa,
                    tipo='R',
                    ativo=True,
                    aceita_lancamento=True,
                    nivel=3,
                    conta_contabil__isnull=False,
                )
                .select_related('conta_pai__conta_pai', 'conta_contabil')
                .order_by('codigo')
            )
            self.fields['plano_contas'].queryset = categorias
            self.fields['plano_contas'].required = categorias.exists()

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
        if cleaned.get('quitar_ao_lancar'):
            valor_original = cleaned.get('valor_original')
            valor_pago = cleaned.get('valor_pago_inicial')
            if not cleaned.get('data_pagamento_inicial'):
                self.add_error('data_pagamento_inicial', 'Informe a data do recebimento.')
            if not cleaned.get('forma_pagamento_utilizada'):
                self.add_error('forma_pagamento_utilizada', 'Informe a forma de recebimento.')
            if cleaned.get('modo_baixa') == 'integral' and valor_original and not valor_pago:
                valor_pago = valor_original
                cleaned['valor_pago_inicial'] = valor_pago
            if not valor_pago:
                self.add_error('valor_pago_inicial', 'Informe o valor recebido.')
            elif valor_original and valor_pago > valor_original:
                self.add_error('valor_pago_inicial', 'O valor recebido não pode ser maior que o valor do título.')
        return cleaned


class BaixaContaReceberForm(forms.Form):
    """Registro de recebimento (baixa) de uma conta a receber."""

    data_pagamento = forms.DateField(
        label='Data do recebimento',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
        initial=timezone.localdate,
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
    bandeira = forms.ChoiceField(
        choices=BANDEIRAS_CARTAO,
        required=False,
        label='Bandeira do cartão (opcional)',
    )
    numero_parcelas = campo_parcelas()
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

    def __init__(self, *args, filial=None, conta=None, pagamento=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.conta = conta
        self.pagamento = pagamento
        if filial:
            configurar_forma_pagamento(self, (
                FormaPagamento.objects
                .filter(empresa=filial.empresa, ativo=True)
                .order_by('descricao')
            ))
            self.fields['conta_bancaria'].queryset = (
                ContaBancaria.objects.for_filial(filial)
                .filter(ativo=True)
                .order_by('descricao')
            )
        if conta:
            self.fields['valor_pago'].initial = conta.valor_saldo
            self.fields['bandeira'].initial = conta.bandeira_recebimento
            self.fields['numero_parcelas'].initial = conta.parcelas_recebimento
            if conta.forma_pagamento_id:
                self.fields['forma_pagamento'].initial = conta.forma_pagamento_id
                if conta.conta_bancaria_id:
                    self.fields['conta_bancaria'].initial = conta.conta_bancaria_id
                elif conta.forma_pagamento.conta_bancaria_padrao_id:
                    self.fields['conta_bancaria'].initial = conta.forma_pagamento.conta_bancaria_padrao_id
        if pagamento:
            self.fields['data_pagamento'].initial = pagamento.data_pagamento
            self.fields['valor_pago'].initial = pagamento.valor_pago
            self.fields['valor_juros'].initial = pagamento.valor_juros
            self.fields['valor_multa'].initial = pagamento.valor_multa
            self.fields['valor_desconto'].initial = pagamento.valor_desconto
            self.fields['forma_pagamento'].initial = pagamento.forma_pagamento_id
            self.fields['conta_bancaria'].initial = pagamento.conta_bancaria_id
            self.fields['bandeira'].initial = pagamento.bandeira
            self.fields['numero_parcelas'].initial = pagamento.numero_parcelas
            self.fields['observacao'].initial = pagamento.observacao

    def clean(self):
        cleaned = super().clean()
        cleaned.setdefault('valor_juros', Decimal('0'))
        cleaned.setdefault('valor_multa', Decimal('0'))
        cleaned.setdefault('valor_desconto', Decimal('0'))
        cleaned = limpar_dados_cartao(self, cleaned)
        valor_pago = cleaned.get('valor_pago')
        if self.conta and valor_pago:
            disponivel = self.conta.valor_saldo or Decimal('0')
            if self.pagamento:
                disponivel += self.pagamento.valor_pago or Decimal('0')
            if valor_pago > disponivel:
                self.add_error(
                    'valor_pago',
                    f'O valor recebido não pode passar do valor restante de R$ {disponivel:.2f}.',
                )
        return cleaned
