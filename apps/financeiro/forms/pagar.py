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
from apps.financeiro.models.receber_pagar import ContaPagar, MetaDespesaPessoal

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

    descricao_despesa = forms.CharField(
        max_length=180,
        label='Descrição da despesa',
        widget=forms.TextInput(attrs={'placeholder': 'Ex.: Mensalidade do sistema de gestão'}),
    )
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
    intervalo_recorrencia_dias = forms.IntegerField(
        min_value=1, max_value=365, initial=30, required=False,
        label='Intervalo em dias',
        widget=forms.NumberInput(attrs={'min': '1', 'max': '365'}),
    )
    quantidade_recorrencias = forms.IntegerField(
        min_value=2, max_value=60, initial=12, required=False,
        label='Quantidade de ocorrências',
        widget=forms.NumberInput(attrs={'min': '2', 'max': '60'}),
    )
    regra_vencimento_mensal = forms.ChoiceField(
        choices=ContaPagar.RegraVencimentoMensal.choices,
        initial=ContaPagar.RegraVencimentoMensal.DATA_INFORMADA,
        required=False,
        label='Vencimento de cada mês',
    )
    dia_vencimento_mensal = forms.IntegerField(
        min_value=1, max_value=31, required=False,
        label='Dia do mês',
        widget=forms.NumberInput(attrs={'min': '1', 'max': '31', 'placeholder': 'Ex.: 10'}),
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
        label='Levar em conta apenas dias úteis',
    )
    antecipar_vencimento_dia_util = forms.BooleanField(
        required=False,
        initial=False,
        label='Antecipar para o dia útil anterior',
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
                .select_related('conta_bancaria_padrao')
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
            if (
                cleaned.get('frequencia_recorrencia') == ContaPagar.FrequenciaRecorrencia.PERSONALIZADA
                and not cleaned.get('intervalo_recorrencia_dias')
            ):
                self.add_error('intervalo_recorrencia_dias', 'Informe o intervalo entre os titulos.')
            if not cleaned.get('quantidade_recorrencias'):
                self.add_error('quantidade_recorrencias', 'Informe quantos títulos devem ser gerados.')
            frequencias_mensais = {
                ContaPagar.FrequenciaRecorrencia.MENSAL,
                ContaPagar.FrequenciaRecorrencia.TRIMESTRAL,
                ContaPagar.FrequenciaRecorrencia.SEMESTRAL,
                ContaPagar.FrequenciaRecorrencia.ANUAL,
            }
            if cleaned.get('frequencia_recorrencia') not in frequencias_mensais:
                cleaned['regra_vencimento_mensal'] = ContaPagar.RegraVencimentoMensal.DATA_INFORMADA
                cleaned['dia_vencimento_mensal'] = None
            elif (
                cleaned.get('regra_vencimento_mensal') == ContaPagar.RegraVencimentoMensal.DIA_FIXO
                and not cleaned.get('dia_vencimento_mensal')
            ):
                self.add_error('dia_vencimento_mensal', 'Informe o dia do mês.')
            elif cleaned.get('regra_vencimento_mensal') != ContaPagar.RegraVencimentoMensal.DIA_FIXO:
                cleaned['dia_vencimento_mensal'] = None
            if quitar_ao_lancar:
                self.add_error('quitar_ao_lancar', 'Títulos recorrentes não podem ser quitados no lançamento.')
        else:
            cleaned['frequencia_recorrencia'] = ''
            cleaned['quantidade_recorrencias'] = 1
            cleaned['intervalo_recorrencia_dias'] = None
            cleaned['regra_vencimento_mensal'] = ContaPagar.RegraVencimentoMensal.DATA_INFORMADA
            cleaned['dia_vencimento_mensal'] = None
        if quitar_ao_lancar:
            data_pagamento = cleaned.get('data_pagamento_imediato')
            if not data_pagamento:
                self.add_error('data_pagamento_imediato', 'Informe a data do pagamento.')
            if not cleaned.get('forma_pagamento_utilizada'):
                self.add_error('forma_pagamento_utilizada', 'Informe a forma realmente utilizada.')
            forma = cleaned.get('forma_pagamento_utilizada')
            if forma and not cleaned.get('conta_bancaria_pagamento'):
                cleaned['conta_bancaria_pagamento'] = forma.conta_bancaria_padrao
        else:
            cleaned['data_pagamento_imediato'] = None
            cleaned['forma_pagamento_utilizada'] = None
            cleaned['conta_bancaria_pagamento'] = None
            cleaned['comprovante_pagamento'] = None
        return cleaned


class DespesaPagaForm(forms.Form):
    """Registro direto de uma despesa que ja foi paga."""

    descricao_despesa = forms.CharField(
        max_length=180,
        label='Descrição da despesa',
        widget=forms.TextInput(attrs={'placeholder': 'Ex.: Gasolina, mensalidade do sistema ou material'}),
    )
    tipo_lancamento = forms.ChoiceField(
        choices=(
            (ContaPagar.TipoLancamento.FORNECEDOR, 'Fornecedor'),
            (ContaPagar.TipoLancamento.FUNCIONARIO, 'Funcionario'),
        ),
        initial=ContaPagar.TipoLancamento.FORNECEDOR,
        widget=forms.HiddenInput,
    )
    fornecedor = FornecedorChoiceField(
        queryset=Fornecedor.objects.none(), required=False, label='Fornecedor',
    )
    funcionario = FuncionarioChoiceField(
        queryset=Funcionario.objects.none(), required=False, label='Funcionario',
    )
    valor_original = forms.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal('0.01'),
        label='Valor pago (R$)', widget=VALOR_WIDGET,
    )
    plano_contas = CategoriaFinanceiraChoiceField(
        queryset=PlanoContas.objects.none(), required=True,
        label='Categoria especifica',
    )
    forma_pagamento_utilizada = forms.ModelChoiceField(
        queryset=FormaPagamento.objects.none(), required=True,
        label='Forma de pagamento',
    )
    conta_bancaria_pagamento = forms.ModelChoiceField(
        queryset=ContaBancaria.objects.none(), required=False,
        label='Conta usada',
    )
    observacao = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}), required=False,
        label='Observacao',
    )
    comprovante_pagamento = forms.FileField(
        required=False, label='Comprovante', widget=COMPROVANTE_WIDGET,
        validators=[validar_comprovante],
    )

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.categoria_grupos = []
        self.categoria_subgrupos = []
        self.contas_contabeis = []
        if not filial:
            return

        self.fields['fornecedor'].queryset = (
            Fornecedor.objects.for_filial(filial).filter(ativo=True).order_by('razao_social')
        )
        self.fields['funcionario'].queryset = (
            Funcionario.objects.for_filial(filial).filter(ativo=True).order_by('nome')
        )
        self.fields['forma_pagamento_utilizada'].queryset = (
            FormaPagamento.objects.filter(empresa=filial.empresa, ativo=True)
            .select_related('conta_bancaria_padrao').order_by('descricao')
        )
        self.fields['conta_bancaria_pagamento'].queryset = (
            ContaBancaria.objects.for_filial(filial).filter(ativo=True).order_by('descricao')
        )
        self.fields['plano_contas'].queryset = (
            PlanoContas.objects.filter(
                empresa=filial.empresa, tipo='D', nivel=3, ativo=True,
                aceita_lancamento=True, conta_contabil__isnull=False,
            ).select_related('conta_pai__conta_pai', 'conta_contabil').order_by('codigo')
        )
        self.categoria_grupos = list(
            PlanoContas.objects.filter(
                empresa=filial.empresa, tipo='D', nivel=1, ativo=True,
            ).order_by('codigo')
        )
        self.categoria_subgrupos = list(
            PlanoContas.objects.filter(
                empresa=filial.empresa, tipo='D', nivel=2, ativo=True,
            ).select_related('conta_pai').order_by('codigo')
        )
        from apps.financeiro.models.plano_contabil import PlanoContabil
        self.contas_contabeis = list(
            PlanoContabil.objects.filter(
                empresa=filial.empresa,
                tipo_conta=PlanoContabil.TipoConta.ANALITICA,
                ativo=True,
            ).order_by('ordem')
        )

    def clean(self):
        cleaned = super().clean()
        forma = cleaned.get('forma_pagamento_utilizada')
        if forma and not cleaned.get('conta_bancaria_pagamento'):
            cleaned['conta_bancaria_pagamento'] = forma.conta_bancaria_padrao
        tipo = cleaned.get('tipo_lancamento')
        if tipo == ContaPagar.TipoLancamento.FUNCIONARIO:
            cleaned['fornecedor'] = None
            if not cleaned.get('funcionario'):
                self.add_error('funcionario', 'Selecione o funcionario que recebeu o pagamento.')
        else:
            cleaned['funcionario'] = None
            if not cleaned.get('fornecedor'):
                self.add_error('fornecedor', 'Selecione o fornecedor que recebeu o pagamento.')
        return cleaned


class MetaDespesaPessoalForm(forms.ModelForm):
    class Meta:
        model = MetaDespesaPessoal
        fields = ["tipo_meta", "valor_fixo", "percentual", "meses_media", "ativo"]
        labels = {
            "tipo_meta": "Tipo de meta",
            "valor_fixo": "Valor fixo mensal (R$)",
            "percentual": "Percentual do faturamento (%)",
            "meses_media": "Meses para media",
            "ativo": "Meta ativa",
        }
        widgets = {
            "valor_fixo": forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}),
            "percentual": forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}),
            "meses_media": forms.NumberInput(attrs={"min": "2", "max": "24"}),
        }

    def clean(self):
        cleaned = super().clean()
        tipo_meta = cleaned.get("tipo_meta")
        valor_fixo = cleaned.get("valor_fixo") or Decimal("0")
        percentual = cleaned.get("percentual") or Decimal("0")
        meses_media = cleaned.get("meses_media") or 3

        if tipo_meta == MetaDespesaPessoal.TipoMeta.VALOR_FIXO:
            if valor_fixo <= 0:
                self.add_error("valor_fixo", "Informe um valor fixo maior que zero.")
            cleaned["percentual"] = Decimal("0")
            cleaned["meses_media"] = 3
        else:
            if percentual <= 0:
                self.add_error("percentual", "Informe um percentual maior que zero.")
            if meses_media < 2 or meses_media > 24:
                self.add_error("meses_media", "Use entre 2 e 24 meses.")
            cleaned["valor_fixo"] = Decimal("0")
        return cleaned


class ContaPagarEdicaoAdminForm(forms.Form):
    """Correcao administrativa de um titulo e da baixa selecionada."""

    descricao_despesa = forms.CharField(
        max_length=180, label='Descrição da despesa',
    )
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
    plano_contas = CategoriaFinanceiraChoiceField(
        queryset=PlanoContas.objects.none(), required=False,
        label='Categoria financeira',
        help_text='A conta contabil vinculada sera atualizada automaticamente.',
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
        self.categoria_grupos = []
        self.categoria_subgrupos = []
        self.categorias_especificas = []
        self.categoria_grupo_id = ''
        self.categoria_subgrupo_id = ''
        self.categoria_especifica_id = ''
        if conta is not None:
            if self.pagamento is None:
                self.pagamento = conta.pagamentos.order_by(
                    '-data_pagamento', '-created_at', '-pk',
                ).first()
            kwargs.setdefault('initial', {
                'descricao_despesa': conta.descricao_exibicao,
                'fornecedor': conta.fornecedor_id,
                'valor_original': conta.valor_original,
                'data_vencimento': conta.data_vencimento,
                'data_competencia': conta.data_competencia,
                'forma_pagamento_prevista': conta.forma_pagamento_prevista_id,
                'plano_contas': conta.plano_contas_id,
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
            ).select_related('conta_bancaria_padrao').order_by('descricao')
            self.fields['forma_pagamento_prevista'].queryset = formas
            self.fields['forma_pagamento'].queryset = formas
            self.fields['plano_contas'].queryset = (
                PlanoContas.objects.filter(
                    empresa=filial.empresa, tipo='D', nivel=3, ativo=True,
                    aceita_lancamento=True, conta_contabil__isnull=False,
                )
                .select_related('conta_pai__conta_pai', 'conta_contabil')
                .order_by('codigo')
            )
            categorias_base = PlanoContas.objects.filter(
                empresa=filial.empresa, tipo='D', ativo=True,
            )
            self.categoria_grupos = list(
                categorias_base.filter(nivel=1).order_by('codigo')
            )
            self.categoria_subgrupos = list(
                categorias_base.filter(nivel=2)
                .select_related('conta_pai')
                .order_by('codigo')
            )
            self.categorias_especificas = list(self.fields['plano_contas'].queryset)

            categoria_id = self.data.get('plano_contas') if self.is_bound else (
                conta.plano_contas_id if conta else None
            )
            categoria = next(
                (item for item in self.categorias_especificas if str(item.pk) == str(categoria_id)),
                None,
            )
            if categoria:
                self.categoria_especifica_id = str(categoria.pk)
                self.categoria_subgrupo_id = str(categoria.conta_pai_id or '')
                self.categoria_grupo_id = str(
                    categoria.conta_pai.conta_pai_id if categoria.conta_pai_id else ''
                )
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
                .select_related('conta_bancaria_padrao')
                .order_by('descricao')
            )
            self.fields['conta_bancaria'].queryset = (
                ContaBancaria.objects.for_filial(filial)
                .filter(ativo=True)
                .order_by('descricao')
            )
        if conta:
            self.fields['valor_pago'].initial = conta.valor_saldo
            forma_inicial = conta.forma_pagamento_prevista or conta.forma_pagamento
            self.fields['forma_pagamento'].initial = forma_inicial
            if conta.conta_bancaria_id:
                self.fields['conta_bancaria'].initial = conta.conta_bancaria_id
            elif forma_inicial and forma_inicial.conta_bancaria_padrao_id:
                self.fields['conta_bancaria'].initial = forma_inicial.conta_bancaria_padrao_id

    def clean(self):
        cleaned = super().clean()
        cleaned.setdefault('valor_juros', Decimal('0'))
        cleaned.setdefault('valor_multa', Decimal('0'))
        cleaned.setdefault('valor_desconto', Decimal('0'))
        forma = cleaned.get('forma_pagamento')
        if forma and not cleaned.get('conta_bancaria'):
            cleaned['conta_bancaria'] = forma.conta_bancaria_padrao
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
