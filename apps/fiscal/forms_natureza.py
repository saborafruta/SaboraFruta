"""
Configurações fiscais das naturezas de operação.

É A TELA QUE TIRA O FISCAL DO CÓDIGO. Sem ela a parametrização existe só no
banco, e mudar um CFOP volta a ser tarefa de quem tem acesso ao servidor — que
é exatamente o que a tabela veio evitar.
"""
from django import forms

from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.produtos.models import Produto

BASE_INPUT_CLASS = 'form-input w-full'


class NaturezaOperacaoForm(forms.ModelForm):

    class Meta:
        model = NaturezaOperacao
        fields = [
            'codigo', 'descricao', 'especie', 'movimenta_estoque',
            'tipo_operacao_estoque', 'gera_financeiro', 'exige_destinatario',
            'entra_no_mdfe', 'ativo', 'observacao',
        ]
        widgets = {'observacao': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        for nome, campo in self.fields.items():
            if isinstance(campo.widget, forms.CheckboxInput):
                continue
            campo.widget.attrs['class'] = BASE_INPUT_CLASS
        self.fields['codigo'].widget.attrs['placeholder'] = 'remessa_venda_fora'
        self.fields['descricao'].widget.attrs['placeholder'] = (
            'Remessa para venda fora do estabelecimento'
        )
        self.fields['tipo_operacao_estoque'].required = False
        self.fields['observacao'].required = False

    def clean_codigo(self):
        codigo = (self.cleaned_data.get('codigo') or '').strip().lower()
        existe = NaturezaOperacao.objects.filter(filial=self.filial, codigo=codigo)
        if self.instance.pk:
            existe = existe.exclude(pk=self.instance.pk)
        if existe.exists():
            raise forms.ValidationError(
                'Já existe uma natureza com este código nesta filial.'
            )
        return codigo


class RegraNaturezaForm(forms.ModelForm):
    """
    Uma regra: o alvo dela e os números que ela devolve.

    CAMPO VAZIO SIGNIFICA "QUALQUER". É o que permite cadastrar o geral uma vez
    e tratar só as exceções, em vez de repetir tudo para cada combinação de UF,
    regime e produto.
    """

    class Meta:
        model = RegraNaturezaOperacao
        fields = [
            'uf_origem', 'uf_destino', 'somente_interestadual',
            'regime_tributario', 'ncm', 'produto',
            'cfop', 'cst_icms', 'csosn', 'cst_pis', 'cst_cofins', 'cst_ipi',
            'aliquota_icms', 'reducao_base_icms', 'aliquota_ipi',
            'aliquota_pis', 'aliquota_cofins',
            'finalidade_nfe', 'natureza_operacao_texto',
            'informacoes_complementares',
            'vigencia_inicio', 'vigencia_fim', 'ativo',
        ]
        widgets = {
            # `format` ISO: com pt-br o Django renderiza 27/08/2026 e o
            # `<input type="date">` descarta, mostrando o campo vazio.
            'vigencia_inicio': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'vigencia_fim': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'informacoes_complementares': forms.Textarea(attrs={'rows': 2}),
        }

    PLACEHOLDERS = {
        'uf_origem': 'qualquer', 'uf_destino': 'qualquer',
        'ncm': 'qualquer', 'cfop': '5904',
        'cst_icms': '', 'csosn': '400', 'cst_pis': '49', 'cst_cofins': '49',
        'natureza_operacao_texto': 'usa a descrição da natureza',
    }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        self.fields['produto'].queryset = Produto.objects.for_filial(filial).filter(ativo=True)
        self.fields['produto'].empty_label = '— qualquer produto —'
        for nome, campo in self.fields.items():
            if isinstance(campo.widget, forms.CheckboxInput):
                continue
            campo.widget.attrs['class'] = BASE_INPUT_CLASS
            if nome in self.PLACEHOLDERS:
                campo.widget.attrs['placeholder'] = self.PLACEHOLDERS[nome]
            if nome != 'cfop':
                campo.required = False
        for nome in ('uf_origem', 'uf_destino', 'cfop'):
            campo = self.fields[nome]
            campo.widget.attrs['style'] = 'text-transform:uppercase;'

    def _maiuscula(self, campo):
        return (self.cleaned_data.get(campo) or '').strip().upper()

    def clean_uf_origem(self):
        return self._maiuscula('uf_origem')

    def clean_uf_destino(self):
        return self._maiuscula('uf_destino')

    def clean(self):
        dados = super().clean()
        if dados.get('uf_destino') and dados.get('somente_interestadual'):
            self.add_error(
                'somente_interestadual',
                'Escolha um destino OU marque interestadual — as duas coisas '
                'juntas descrevem alvos diferentes.',
            )
        inicio, fim = dados.get('vigencia_inicio'), dados.get('vigencia_fim')
        if inicio and fim and fim < inicio:
            self.add_error('vigencia_fim', 'A vigência termina antes de começar.')
        # CST E CSOSN SAO EXCLUDENTES: quem manda e' o regime da empresa, e
        # mandar os dois na nota e' rejeicao certa na SEFAZ.
        if dados.get('cst_icms') and dados.get('csosn'):
            self.add_error(
                'csosn',
                'Preencha CST ou CSOSN, não os dois — o regime da empresa '
                'define qual vale, e mandar ambos é rejeição na SEFAZ.',
            )
        return dados
