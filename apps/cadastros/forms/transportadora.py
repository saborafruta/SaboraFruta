from django import forms

from apps.cadastros.models import Motorista, Representante, Transportadora, Veiculo


class TransportadoraForm(forms.ModelForm):
    class Meta:
        model = Transportadora
        exclude = ['filial', 'created_at', 'updated_at']
        widgets = {
            'cnpj': forms.TextInput(attrs={'maxlength': '14', 'placeholder': '00000000000000'}),
            'cep': forms.TextInput(attrs={'maxlength': '8', 'placeholder': '00000000'}),
            'codigo_municipio_ibge': forms.TextInput(attrs={'maxlength': '7', 'placeholder': '0000000'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-input w-full')


class MotoristaForm(forms.ModelForm):
    class Meta:
        model = Motorista
        exclude = ['filial', 'created_at', 'updated_at']
        widgets = {
            'cpf': forms.TextInput(attrs={'maxlength': '14', 'placeholder': '000.000.000-00'}),
            'validade_cnh': forms.DateInput(attrs={'type': 'date'}),
            'cep': forms.TextInput(attrs={'maxlength': '8', 'placeholder': '00000000'}),
            'observacao': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial is not None:
            self.fields['transportadora'].queryset = Transportadora.objects.for_filial(filial).filter(ativo=True)
        self.fields['transportadora'].required = False
        self.fields['cpf'].required = True
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-input w-full')

    def clean_cpf(self):
        cpf = ''.join(filter(str.isdigit, self.cleaned_data.get('cpf') or ''))
        if len(cpf) != 11:
            raise forms.ValidationError('Informe um CPF com 11 dígitos.')
        return cpf


class VeiculoForm(forms.ModelForm):
    class Meta:
        model = Veiculo
        exclude = ['filial', 'created_at', 'updated_at']
        widgets = {
            'placa': forms.TextInput(attrs={'maxlength': '8', 'placeholder': 'ABC1234', 'style': 'text-transform:uppercase;'}),
            'renavam': forms.TextInput(attrs={'maxlength': '15'}),
            'chassi': forms.TextInput(attrs={'maxlength': '17'}),
            'observacao': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial is not None:
            self.fields['transportadora'].queryset = Transportadora.objects.for_filial(filial).filter(ativo=True)
        self.fields['transportadora'].required = False
        for nome in ('uf_placa', 'tipo_rodado', 'tipo_carroceria', 'tara'):
            self.fields[nome].required = True
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-input w-full')

    def clean_placa(self):
        placa = ''.join(
            caractere
            for caractere in (self.cleaned_data.get('placa') or '').upper()
            if caractere.isalnum()
        )
        if len(placa) != 7:
            raise forms.ValidationError('Informe uma placa com 7 caracteres.')
        return placa

    def clean_tara(self):
        tara = self.cleaned_data.get('tara')
        if tara is None or tara <= 0:
            raise forms.ValidationError('Informe a tara do veículo em kg, maior que zero.')
        return tara


class RepresentanteForm(forms.ModelForm):
    class Meta:
        model = Representante
        exclude = ['filial', 'created_at', 'updated_at']
        widgets = {
            'cpf': forms.TextInput(attrs={'maxlength': '11'}),
        }
