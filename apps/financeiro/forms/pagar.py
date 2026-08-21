"""Formulários de Contas a Pagar."""
from datetime import date
from decimal import Decimal
from pathlib import Path

from django import forms

from apps.cadastros.models import Fornecedor, Funcionario
from apps.financeiro.forms.cadastros import ContaBancariaChoiceField
from apps.financeiro.models.conta_bancaria import ContaBancaria, PlanoContas
from apps.financeiro.models.formas_pagamento import FormaPagamento
from apps.financeiro.forms.plano_contas import CategoriaFinanceiraChoiceField
from apps.financeiro.models.receber_pagar import ContaPagar

VALOR_WIDGET = forms.NumberInput(attrs={
    'step': '0.01',
    'inputmode': 'decimal',
    'data-decimal-places': '2',
})

EXTENSOES_COMPROVANTE = {'.pdf', '.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'}
LIMITE_COMPROVANTE = 10 * 1024 * 1024


def validar_comprovante(arquivo):
    if not arquivo:
        return arquivo
    extensao = Path(arquivo.name).suffix.lower()
    if extensao not in EXTENSOES_COMPROVANTE:
        raise forms.ValidationError('Envie uma foto ou PDF válido.')
    if arquivo.size > LIMITE_COMPROVANTE:
        raise forms.ValidationError('O comprovante deve ter no máximo 10 MB.')
    return arquivo


COMPROVANTE_WIDGET = forms.ClearableFileInput(attrs={
    'accept': 'image/*,.pdf,application/pdf',
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
        max_length=44,
        required=False,
        label='NF ou chave de acesso',
        help_text='Digite o número da NF ou leia a chave de 44 dígitos.',
    )
    chave_acesso_nfe = forms.CharField(required=False, widget=forms.HiddenInput)
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
    forma_pagamento_prevista = forms.ModelChoiceField(
        queryset=FormaPagamento.objects.none(),
        required=False,
        label='Forma de pagamento prevista',
        help_text='Opcional. Confirme ou altere a forma realmente utilizada ao quitar.',
    )
    quitar_ao_lancar = forms.BooleanField(
        required=False,
        label='Lançar e quitar agora',
    )
    data_pagamento_imediato = forms.DateField(
        required=False,
        initial=date.today,
        label='Data do pagamento',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    forma_pagamento_utilizada = forms.ModelChoiceField(
        queryset=FormaPagamento.objects.none(),
        required=False,
        label='Forma utilizada',
    )
    conta_bancaria_pagamento = forms.ModelChoiceField(
        queryset=ContaBancaria.objects.none(),
        required=False,
        label='Conta bancária debitada',
    )
    comprovante_pagamento = forms.FileField(
        required=False,
        label='Comprovante',
        widget=COMPROVANTE_WIDGET,
        validators=[validar_comprovante],
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
            formas_pagamento = (
                FormaPagamento.objects
                .filter(empresa=filial.empresa, ativo=True)
                .order_by('descricao')
            )
            self.fields['forma_pagamento_prevista'].queryset = formas_pagamento
            self.fields['forma_pagamento_utilizada'].queryset = formas_pagamento
            self.fields['conta_bancaria_pagamento'].queryset = (
                ContaBancaria.objects.for_filial(filial)
                .filter(ativo=True)
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
        codigo_nota = ''.join(
            caractere
            for caractere in (cleaned.get('nota_fiscal_fornecedor') or '')
            if caractere.isdigit()
        )
        chave_informada = ''.join(
            caractere
            for caractere in (cleaned.get('chave_acesso_nfe') or '')
            if caractere.isdigit()
        )
        if len(codigo_nota) == 44:
            chave_informada = codigo_nota
        if chave_informada:
            if len(chave_informada) != 44:
                self.add_error('nota_fiscal_fornecedor', 'A chave da NF-e deve ter 44 dígitos.')
            else:
                cleaned['chave_acesso_nfe'] = chave_informada
                cleaned['nota_fiscal_fornecedor'] = (
                    chave_informada[25:34].lstrip('0') or chave_informada[25:34]
                )
        elif len(codigo_nota) > 20:
            self.add_error(
                'nota_fiscal_fornecedor',
                'Informe o número da NF ou a chave completa com 44 dígitos.',
            )
        else:
            cleaned['chave_acesso_nfe'] = ''
            cleaned['nota_fiscal_fornecedor'] = (
                cleaned.get('nota_fiscal_fornecedor') or ''
            ).strip()
        parcela = cleaned.get('parcela')
        total = cleaned.get('total_parcelas')
        tipo = cleaned.get('tipo_lancamento')
        funcionario = cleaned.get('funcionario')
        recorrente = cleaned.get('recorrente')
        quitar_ao_lancar = cleaned.get('quitar_ao_lancar')
        if tipo == ContaPagar.TipoLancamento.FORNECEDOR:
            cleaned['funcionario'] = None
        elif tipo == ContaPagar.TipoLancamento.FUNCIONARIO:
            cleaned['fornecedor'] = None
            if not funcionario:
                self.add_error('funcionario', 'Selecione o funcionario que recebera este pagamento.')
        elif tipo == ContaPagar.TipoLancamento.ENCARGO:
            cleaned['fornecedor'] = None
        if parcela and total and parcela > total:
            self.add_error('parcela', 'Parcela não pode ser maior que o total de parcelas.')
        if recorrente:
            if not cleaned.get('frequencia_recorrencia'):
                self.add_error('frequencia_recorrencia', 'Informe a periodicidade.')
            if not cleaned.get('quantidade_recorrencias'):
                self.add_error('quantidade_recorrencias', 'Informe quantos títulos devem ser gerados.')
            if quitar_ao_lancar:
                self.add_error('quitar_ao_lancar', 'Títulos recorrentes não podem ser quitados no lançamento.')
        else:
            cleaned['frequencia_recorrencia'] = ''
            cleaned['quantidade_recorrencias'] = 1
        if quitar_ao_lancar:
            data_pagamento = cleaned.get('data_pagamento_imediato')
            if not data_pagamento:
                self.add_error('data_pagamento_imediato', 'Informe a data do pagamento.')
            if not cleaned.get('forma_pagamento_utilizada'):
                self.add_error('forma_pagamento_utilizada', 'Informe a forma realmente utilizada.')
        else:
            cleaned['data_pagamento_imediato'] = None
            cleaned['forma_pagamento_utilizada'] = None
            cleaned['conta_bancaria_pagamento'] = None
            cleaned['comprovante_pagamento'] = None
        return cleaned


class ContaPagarEdicaoAdminForm(forms.Form):
    """Correcao administrativa de um titulo e da baixa selecionada."""

    fornecedor = FornecedorChoiceField(
        queryset=Fornecedor.objects.none(), required=False, label='Fornecedor',
    )
    valor_original = forms.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal('0.01'),
        label='Valor do titulo (R$)', widget=VALOR_WIDGET,
    )
    data_vencimento = forms.DateField(
        label='Vencimento',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
    )
    data_competencia = forms.DateField(
        required=False, label='Competencia',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
    )
    forma_pagamento_prevista = forms.ModelChoiceField(
        queryset=FormaPagamento.objects.none(), required=False,
        label='Forma prevista',
    )
    data_pagamento = forms.DateField(
        required=False, label='Data do pagamento',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
    )
    forma_pagamento = forms.ModelChoiceField(
        queryset=FormaPagamento.objects.none(), required=False,
        label='Forma utilizada',
    )
    conta_bancaria = ContaBancariaChoiceField(
        queryset=ContaBancaria.objects.none(), required=False,
        label='Conta bancaria',
    )
    observacao = forms.CharField(
        required=False, label='Observacao',
        widget=forms.Textarea(attrs={'rows': 3}),
    )
    motivo = forms.CharField(
        max_length=300, label='Motivo da alteracao',
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Explique por que o lancamento esta sendo corrigido',
        }),
    )

    def __init__(self, *args, filial=None, conta=None, pagamento=None, **kwargs):
        self.conta = conta
        self.pagamento = pagamento
        if conta is not None:
            if self.pagamento is None:
                self.pagamento = conta.pagamentos.order_by(
                    '-data_pagamento', '-created_at', '-pk',
                ).first()
            kwargs.setdefault('initial', {
                'fornecedor': conta.fornecedor_id,
                'valor_original': conta.valor_original,
                'data_vencimento': conta.data_vencimento,
                'data_competencia': conta.data_competencia,
                'forma_pagamento_prevista': conta.forma_pagamento_prevista_id,
                'data_pagamento': self.pagamento.data_pagamento if self.pagamento else None,
                'forma_pagamento': self.pagamento.forma_pagamento_id if self.pagamento else conta.forma_pagamento_id,
                'conta_bancaria': self.pagamento.conta_bancaria_id if self.pagamento else conta.conta_bancaria_id,
                'observacao': conta.observacao,
            })
        super().__init__(*args, **kwargs)
        if filial:
            self.fields['fornecedor'].queryset = (
                Fornecedor.objects.for_filial(filial)
                .filter(ativo=True)
                .order_by('razao_social')
            )
            formas = FormaPagamento.objects.filter(
                empresa=filial.empresa, ativo=True,
            ).order_by('descricao')
            self.fields['forma_pagamento_prevista'].queryset = formas
            self.fields['forma_pagamento'].queryset = formas
            self.fields['conta_bancaria'].queryset = (
                ContaBancaria.objects.for_filial(filial)
                .filter(ativo=True)
                .order_by('descricao', 'banco_nome')
            )

        if self.pagamento is None:
            for nome in ('data_pagamento', 'forma_pagamento', 'conta_bancaria'):
                self.fields.pop(nome)

    def clean(self):
        cleaned = super().clean()
        if self.pagamento and not cleaned.get('data_pagamento'):
            self.add_error('data_pagamento', 'Informe a data em que o pagamento ocorreu.')
        if (
            self.pagamento
            and cleaned.get('data_pagamento')
            and self.conta
            and cleaned['data_pagamento'] < self.conta.data_emissao
        ):
            self.add_error('data_pagamento', 'A data do pagamento nao pode ser anterior a emissao.')
        return cleaned


class PagamentoContaPagarForm(forms.Form):
    """Registro de pagamento (baixa) de uma conta a pagar."""

    data_pagamento = forms.DateField(
        label='Data do pagamento',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
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
        label='Forma utilizada',
    )
    conta_bancaria = ContaBancariaChoiceField(
        queryset=ContaBancaria.objects.none(),
        required=False,
        label='Conta bancária',
        help_text='Conta debitada no pagamento.',
    )
    referencia_pagamento = forms.CharField(
        max_length=100,
        required=False,
        label='Referência da transação',
        help_text='ID do PIX, autenticação bancária, nosso número ou outra referência.',
    )
    comprovante = forms.FileField(
        required=False,
        label='Comprovante',
        widget=COMPROVANTE_WIDGET,
        validators=[validar_comprovante],
    )
    observacao = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}),
        required=False,
        label='Observação',
    )

    def __init__(self, *args, filial=None, conta=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.conta = conta
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
            self.fields['forma_pagamento'].initial = (
                conta.forma_pagamento_prevista_id or conta.forma_pagamento_id
            )

    def clean(self):
        cleaned = super().clean()
        cleaned.setdefault('valor_juros', Decimal('0'))
        cleaned.setdefault('valor_multa', Decimal('0'))
        cleaned.setdefault('valor_desconto', Decimal('0'))
        data_pagamento = cleaned.get('data_pagamento')
        if self.conta and data_pagamento and data_pagamento < self.conta.data_emissao:
            self.add_error(
                'data_pagamento',
                'A data do pagamento não pode ser anterior à emissão.',
            )
        valor_pago = cleaned.get('valor_pago')
        juros = cleaned.get('valor_juros') or Decimal('0')
        multa = cleaned.get('valor_multa') or Decimal('0')
        desconto = cleaned.get('valor_desconto') or Decimal('0')
        if self.conta:
            saldo_atualizado = self.conta.valor_saldo + juros + multa - desconto
            if saldo_atualizado <= Decimal('0'):
                self.add_error(
                    'valor_desconto',
                    'O desconto não pode zerar ou ultrapassar o saldo do título.',
                )
            elif valor_pago and valor_pago > saldo_atualizado:
                self.add_error(
                    'valor_pago',
                    f'O valor não pode superar o saldo atualizado de R$ {saldo_atualizado:.2f}.',
                )
        return cleaned
