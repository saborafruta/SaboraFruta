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
        # ROTULOS DITOS AQUI, e nao no modelo: mexer em `verbose_name` gera
        # migration so' para trocar texto. O padrao do Django vinha do nome do
        # campo -- "Linha producao", "Capacidade dia", "Horas dia" -- sem
        # acento e sem a unidade, que e' o que a pessoa precisa saber para
        # digitar o numero certo.
        labels = {
            'nome': 'Nome',
            'tipo': 'Tipo',
            'linha_producao': 'Linha de produção do ERP',
            'capacidade_dia': 'Capacidade por dia',
            'horas_dia': 'Horas por dia',
            'setup_minutos': 'Setup (minutos)',
            'ativo': 'Recurso ativo',
            'observacao': 'Observação',
        }
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
        self.fields['linha_producao'].empty_label = 'Sem vínculo'

    def save(self, commit=True):
        recurso = super().save(commit=False)
        if self.filial and not recurso.filial_id:
            recurso.filial = self.filial
        if commit:
            recurso.save()
        return recurso
