from django import forms

from apps.cadastros.models import Cliente, ClienteEndereco
from apps.produtos.models import TabelaPreco


class ClienteForm(forms.ModelForm):
    tabela_preco = forms.ModelChoiceField(
        queryset=TabelaPreco.objects.none(),
        required=False,
        empty_label='Padrao (preco cadastrado no produto)',
        label='Tabela de preco',
    )
    cpf_cnpj = forms.CharField(
        required=False,
        max_length=18,
        widget=forms.TextInput(attrs={'placeholder': 'Somente numeros', 'maxlength': '18'}),
    )
    cep = forms.CharField(
        required=False,
        max_length=9,
        widget=forms.TextInput(attrs={
            'placeholder': '00000-000',
            'maxlength': '9',
            'x-on:blur': 'consultarCep($event.target.value)',
        }),
    )

    class Meta:
        model = Cliente
        exclude = ['filial', 'ativo', 'saldo_devedor', 'pontos_fidelidade', 'created_at', 'updated_at']
        widgets = {
            'observacao': forms.Textarea(attrs={'rows': 3}),
            'motivo_bloqueio': forms.Textarea(attrs={'rows': 2}),
            'data_nascimento': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        filial = filial or getattr(self.instance, 'filial', None)
        if filial:
            self.fields['tabela_preco'].queryset = (
                TabelaPreco.objects.for_filial(filial)
                .filter(ativo=True)
                .order_by('descricao')
            )

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


class ClienteEnderecoForm(forms.ModelForm):
    class Meta:
        model = ClienteEndereco
        exclude = ['cliente', 'created_at', 'updated_at']
