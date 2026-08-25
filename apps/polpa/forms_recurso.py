"""O cadastro de linha e máquina."""
from django import forms

from apps.polpa.models import Recurso
from apps.produtos.models import LinhaProducao

ENTRADA = {'class': 'form-input w-full'}
SELECT = {'class': 'form-select w-full'}


class RecursoForm(forms.ModelForm):
    class Meta:
        model = Recurso
        fields = (
            'nome', 'tipo', 'linha_producao', 'capacidade_dia', 'horas_dia',
            'setup_minutos', 'ativo', 'observacao',
        )
        widgets = {
            'nome': forms.TextInput(attrs={**ENTRADA, 'placeholder': 'Despolpadeira 1'}),
            'tipo': forms.Select(attrs=SELECT),
            'linha_producao': forms.Select(attrs=SELECT),
            'capacidade_dia': forms.NumberInput(attrs={**ENTRADA, 'step': '0.001'}),
            'horas_dia': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'setup_minutos': forms.NumberInput(attrs=ENTRADA),
            'observacao': forms.Textarea(attrs={**ENTRADA, 'rows': 2}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        # A LINHA DO ERP e' da EMPRESA, nao da filial -- e por isso o filtro
        # e' por empresa. Filtrar por filial devolveria vazio sempre, e a
        # pessoa concluiria que nao ha' linha cadastrada.
        self.fields['linha_producao'].queryset = (
            LinhaProducao.objects.filter(empresa=filial.empresa, ativo=True)
            if filial else LinhaProducao.objects.none()
        )
        self.fields['linha_producao'].required = False
        self.fields['linha_producao'].empty_label = 'Sem vinculo'

    def save(self, commit=True):
        recurso = super().save(commit=False)
        if self.filial and not recurso.filial_id:
            recurso.filial = self.filial
        if commit:
            recurso.save()
        return recurso
