from django import forms

from apps.cadastros.models import Fornecedor


class FornecedorForm(forms.ModelForm):
    cpf_cnpj = forms.CharField(
        required=False,
        max_length=18,
        widget=forms.TextInput(attrs={'maxlength': '18'}),
    )
    cep = forms.CharField(
        required=False,
        max_length=9,
        widget=forms.TextInput(attrs={
            'maxlength': '9',
            'x-on:blur': 'consultarCep($event.target.value)',
        }),
    )

    class Meta:
        model = Fornecedor
        exclude = [
            'filial', 'nota_qualidade', 'total_entregas', 'entregas_no_prazo',
            'ativo', 'created_at', 'updated_at',
        ]
        widgets = {
            'observacao': forms.Textarea(attrs={'rows': 3}),
        }

    def clean_cpf_cnpj(self):
        valor = ''.join(filter(str.isdigit, self.cleaned_data.get('cpf_cnpj', '') or ''))
        if valor and len(valor) not in (11, 14):
            raise forms.ValidationError('CPF deve ter 11 digitos ou CNPJ deve ter 14.')
        return valor

    def clean_cep(self):
        valor = ''.join(filter(str.isdigit, self.cleaned_data.get('cep', '') or ''))
        if valor and len(valor) != 8:
            raise forms.ValidationError('CEP deve ter 8 digitos.')
        return valor


class FornecedorRapidoForm(forms.ModelForm):
    """Campos essenciais para criar fornecedor durante um lançamento."""

    cpf_cnpj = forms.CharField(required=False, max_length=18)
    cep = forms.CharField(required=False, max_length=9)

    class Meta:
        model = Fornecedor
        fields = [
            'tipo_pessoa', 'razao_social', 'nome_fantasia', 'cpf_cnpj',
            'inscricao_estadual', 'telefone', 'email', 'cep', 'endereco',
            'numero', 'bairro', 'cidade', 'uf', 'codigo_municipio_ibge',
        ]

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial

    def clean_cpf_cnpj(self):
        valor = ''.join(filter(str.isdigit, self.cleaned_data.get('cpf_cnpj', '') or ''))
        if valor and len(valor) not in (11, 14):
            raise forms.ValidationError('CPF deve ter 11 dígitos ou CNPJ deve ter 14.')
        if (
            valor
            and self.filial
            and Fornecedor.objects.for_filial(self.filial).filter(cpf_cnpj=valor).exists()
        ):
            raise forms.ValidationError('Já existe um fornecedor com este CPF/CNPJ nesta filial.')
        return valor

    def clean_cep(self):
        valor = ''.join(filter(str.isdigit, self.cleaned_data.get('cep', '') or ''))
        if valor and len(valor) != 8:
            raise forms.ValidationError('CEP deve ter 8 dígitos.')
        return valor

    def clean_uf(self):
        return (self.cleaned_data.get('uf') or '').strip().upper()
