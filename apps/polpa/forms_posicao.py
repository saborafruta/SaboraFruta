"""Cadastro de posicao dentro da camara e registro de temperatura."""
from django import forms

from apps.polpa.models import Camara, LeituraTemperatura, Posicao

ENTRADA = {'class': 'form-input w-full'}
SELECT = {'class': 'form-select w-full'}


class PosicaoForm(forms.ModelForm):
    class Meta:
        model = Posicao
        fields = (
            'camara', 'corredor', 'rua', 'prateleira', 'posicao',
            'capacidade_kg', 'ativo', 'observacao',
        )
        widgets = {
            'camara': forms.Select(attrs=SELECT),
            'corredor': forms.TextInput(attrs={**ENTRADA, 'placeholder': 'A'}),
            'rua': forms.TextInput(attrs={**ENTRADA, 'placeholder': '3'}),
            'prateleira': forms.TextInput(attrs={**ENTRADA, 'placeholder': 'B'}),
            'posicao': forms.TextInput(attrs={**ENTRADA, 'placeholder': '02'}),
            'capacidade_kg': forms.NumberInput(attrs={**ENTRADA, 'step': '0.001'}),
            'observacao': forms.TextInput(attrs=ENTRADA),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        self.fields['camara'].queryset = (
            Camara.objects.for_filial(filial).filter(ativo=True)
            if filial else Camara.objects.none()
        )
        for campo in ('corredor', 'rua', 'prateleira', 'posicao', 'observacao'):
            self.fields[campo].required = False

    def clean(self):
        dados = super().clean()
        niveis = [
            (dados.get(c) or '').strip()
            for c in ('corredor', 'rua', 'prateleira', 'posicao')
        ]
        # POSICAO SEM NENHUM NIVEL nao enderecaria nada: seria um registro
        # que aponta para a camara inteira, e a camara ja' e' o endereco de
        # quem nao mapeou nada.
        if not any(niveis):
            raise forms.ValidationError(
                'Preencha ao menos um nivel -- corredor, rua, prateleira ou '
                'posicao. Sem nenhum, o endereco seria a camara inteira.'
            )
        return dados

    def save(self, commit=True):
        posicao = super().save(commit=False)
        if self.filial and not posicao.filial_id:
            posicao.filial = self.filial
        if commit:
            posicao.save()
        return posicao


class LeituraForm(forms.ModelForm):
    class Meta:
        model = LeituraTemperatura
        fields = ('camara', 'temperatura', 'medida_em', 'observacao')
        widgets = {
            'camara': forms.Select(attrs=SELECT),
            'temperatura': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'medida_em': forms.DateTimeInput(
                attrs={**ENTRADA, 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'observacao': forms.TextInput(attrs=ENTRADA),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        self.fields['camara'].queryset = (
            Camara.objects.for_filial(filial).filter(ativo=True)
            if filial else Camara.objects.none()
        )
        # A HORA E' OPCIONAL: quem mede na camara anota depois, e exigir o
        # horario exato faria a pessoa digitar qualquer coisa. Vazio vale
        # agora, que e' o caso comum de quem registra na hora.
        self.fields['medida_em'].required = False
        self.fields['observacao'].required = False
