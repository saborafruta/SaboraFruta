from django import forms

from apps.cadastros.forms.cliente import ClienteForm
from apps.cadastros.models import Cliente


class ClientePDVForm(forms.ModelForm):
    """Somente dados cadastrais; nunca altera condições comerciais da venda."""

    cpf_cnpj = forms.CharField(required=False, max_length=18)
    cep = forms.CharField(required=False, max_length=9)
    clean_cpf_cnpj = ClienteForm.clean_cpf_cnpj
    clean_cep = ClienteForm.clean_cep

    class Meta:
        model = Cliente
        fields = (
            'tipo_pessoa', 'razao_social', 'nome_fantasia', 'cpf_cnpj',
            'telefone', 'celular', 'email', 'cep', 'endereco', 'numero',
            'complemento', 'bairro', 'cidade', 'uf',
        )
