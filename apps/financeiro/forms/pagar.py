"""Formulários de Contas a Pagar."""
from datetime import date
from decimal import Decimal

from django import forms

from apps.cadastros.models import Fornecedor, Funcionario
from apps.financeiro.models.conta_bancaria import ContaBancaria, PlanoContas
from apps.financeiro.models.formas_pagamento import FormaPagamento
from apps.financeiro.forms.plano_contas import CategoriaFinanceiraChoiceField
from apps.financeiro.models.receber_pagar import ContaPagar

VALOR_WIDGET = forms.NumberInput(attrs={
    'step': '0.01',
    'inputmode': 'decimal',
    'data-decimal-places': '2',
})


class FornecedorChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, fornecedor):
        principal = fornecedor.nome_fantasia or fornecedor.razao_social
        detalhes = []
        if fornecedor.nome_fantasia and fornecedor.razao_social != principal:
            detalhes.append(fornecedor.razao_social)
        if fornecedor.cpf_cnpj:
            detalhes.append(fornecedor.cpf_cnpj)
        return f"{principal} - {' - '.join(detalhes)}" if detalhes else principal


class FuncionarioChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, funcionario):
        detalhes = [valor for valor in (funcionario.cargo, funcionario.cpf) if valor]
        return f"{funcionario.nome} - {' - '.join(detalhes)}" if detalhes else funcionario.nome


class ContaPagarForm(forms.Form):
    """Lançamento manual de conta a pagar."""

    tipo_lancamento = forms.ChoiceField(
        choices=ContaPagar.TipoLancamento.choices,
        initial=ContaPagar.TipoLancamento.FORNECEDOR,
        widget=forms.HiddenInput,
    )
    fornecedor = FornecedorChoiceField(
        queryset=Fornecedor.objects.none(),
        required=False,
        label='Fornecedor',
        help_text='Opcional para despesas sem fornecedor cadastrado.',
    )
    funcionario = FuncionarioChoiceField(
        queryset=Funcionario.objects.none(),
        required=False,
        label='Funcionario',
    )
    documento_numero = forms.CharField(
        max_length=20,
        required=False,
        label='Nº do documento',
        help_text='Número da NF, boleto ou outro documento de referência.',
    )
    nota_fiscal_fornecedor = forms.CharField(
        max_length=20,
        required=False,
        label='NF do fornecedor',
        help_text='Número da nota fiscal emitida pelo fornecedor.',
    )
    parcela = forms.IntegerField(
        min_value=1,
        initial=1,
        label='Parcela',
        widget=forms.HiddenInput,
    )
    total_parcelas = forms.IntegerField(
        min_value=1,
        initial=1,
        label='Total de parcelas',
        widget=forms.HiddenInput,
    )
    recorrente = forms.BooleanField(required=False, label='Título recorrente')
    frequencia_recorrencia = forms.ChoiceField(
        choices=ContaPagar.FrequenciaRecorrencia.choices,
        initial=ContaPagar.FrequenciaRecorrencia.MENSAL,
        required=False,
        label='Periodicidade',
    )
    quantidade_recorrencias = forms.IntegerField(
        min_value=2, max_value=60, initial=12, required=False,
        label='Quantidade de ocorrências',
        widget=forms.NumberInput(attrs={'min': '2', 'max': '60'}),
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
    ajustar_vencimento_dia_util = forms.BooleanField(
        required=False,
        initial=True,
        label='Ajustar se cair em domingo ou feriado',
    )
    data_competencia = forms.DateField(
        required=False,
        label='Competência',
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text='Mês de competência da despesa (opcional).',
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
        widget=forms.Textarea(attrs={'rows': 4}),
        required=False,
        label='Observação',
    )

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial:
            self.fields['fornecedor'].queryset = (
                Fornecedor.objects.for_filial(filial)
                .filter(ativo=True)
                .order_by('razao_social')
            )
            self.fields['funcionario'].queryset = (
                Funcionario.objects.for_filial(filial)
                .filter(ativo=True)
                .order_by('nome')
            )
            self.fields['forma_pagamento'].queryset = (
                FormaPagamento.objects
                .filter(empresa=filial.empresa, ativo=True)
                .order_by('descricao')
            )
            categorias = (
                PlanoContas.objects
                .filter(
                    empresa=filial.empresa,
                    tipo='D',
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
            subgrupo_ids = categorias.values_list('conta_pai_id', flat=True)
            grupo_ids = categorias.values_list('conta_pai__conta_pai_id', flat=True)
            self.categoria_grupos = list(
                PlanoContas.objects.filter(
                    empresa=filial.empresa, tipo='D', ativo=True, nivel=1,
                    pk__in=grupo_ids,
                ).order_by('codigo')
            )
            self.categoria_subgrupos = list(
                PlanoContas.objects.filter(
                    empresa=filial.empresa, tipo='D', ativo=True, nivel=2,
                    conta_pai__isnull=False, pk__in=subgrupo_ids,
                ).select_related('conta_pai').order_by('codigo')
            )
        else:
            self.categoria_grupos = []
            self.categoria_subgrupos = []

    def clean(self):
        cleaned = super().clean()
        emissao = cleaned.get('data_emissao')
        vencimento = cleaned.get('data_vencimento')
        parcela = cleaned.get('parcela')
        total = cleaned.get('total_parcelas')
        tipo = cleaned.get('tipo_lancamento')
        funcionario = cleaned.get('funcionario')
        recorrente = cleaned.get('recorrente')
        if tipo == ContaPagar.TipoLancamento.FORNECEDOR:
            cleaned['funcionario'] = None
        elif tipo == ContaPagar.TipoLancamento.FUNCIONARIO:
            cleaned['fornecedor'] = None
            if not funcionario:
                self.add_error('funcionario', 'Selecione o funcionario que recebera este pagamento.')
        elif tipo == ContaPagar.TipoLancamento.ENCARGO:
            cleaned['fornecedor'] = None
        if emissao and vencimento and vencimento < emissao:
            self.add_error('data_vencimento', 'Vencimento não pode ser anterior à emissão.')
        if parcela and total and parcela > total:
            self.add_error('parcela', 'Parcela não pode ser maior que o total de parcelas.')
        if recorrente:
            if not cleaned.get('frequencia_recorrencia'):
                self.add_error('frequencia_recorrencia', 'Informe a periodicidade.')
            if not cleaned.get('quantidade_recorrencias'):
                self.add_error('quantidade_recorrencias', 'Informe quantos títulos devem ser gerados.')
        else:
            cleaned['frequencia_recorrencia'] = ''
            cleaned['quantidade_recorrencias'] = 1
        return cleaned


class PagamentoContaPagarForm(forms.Form):
    """Registro de pagamento (baixa) de uma conta a pagar."""

    data_pagamento = forms.DateField(
        label='Data do pagamento',
        widget=forms.DateInput(attrs={'type': 'date'}),
        initial=date.today,
    )
    valor_pago = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0.01'),
        label='Valor pago (R$)',
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
        label='Forma de pagamento',
    )
    conta_bancaria = forms.ModelChoiceField(
        queryset=ContaBancaria.objects.none(),
        required=False,
        label='Conta bancária',
        help_text='Conta debitada no pagamento.',
    )
    comprovante_url = forms.URLField(
        required=False,
        label='URL do comprovante',
        help_text='Link para comprovante de pagamento (opcional).',
        widget=forms.URLInput(attrs={'placeholder': 'https://...'}),
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
