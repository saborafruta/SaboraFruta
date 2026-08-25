"""O cadastro da camara fria."""
from django import forms

from apps.polpa.models import Camara

ENTRADA = {'class': 'form-input w-full'}
SELECT = {'class': 'form-select w-full'}


class CamaraForm(forms.ModelForm):
    class Meta:
        model = Camara
        fields = (
            'nome', 'tipo', 'temperatura_min', 'temperatura_max',
            'capacidade_kg', 'ativo', 'observacao',
        )
        widgets = {
            'nome': forms.TextInput(attrs={**ENTRADA, 'placeholder': 'Camara 1'}),
            'tipo': forms.Select(attrs=SELECT),
            'temperatura_min': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'temperatura_max': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'capacidade_kg': forms.NumberInput(attrs={**ENTRADA, 'step': '0.001'}),
            'observacao': forms.Textarea(attrs={**ENTRADA, 'rows': 2}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial

    def clean(self):
        dados = super().clean()
        minima = dados.get('temperatura_min')
        maxima = dados.get('temperatura_max')
        # EM CONGELADOS OS NUMEROS SAO NEGATIVOS, e e' facil inverter: -18 a
        # -25 parece certo e esta' trocado. A faixa invertida faria toda
        # conferencia de temperatura aprovar o que deveria reprovar.
        if minima is not None and maxima is not None and minima > maxima:
            self.add_error(
                'temperatura_max',
                'A minima ficou acima da maxima -- em congelados, -25 e' + "'" + ' menor que -18.',
            )
        return dados

    def save(self, commit=True):
        camara = super().save(commit=False)
        if self.filial and not camara.filial_id:
            camara.filial = self.filial
        if commit:
            camara.save()
        return camara
