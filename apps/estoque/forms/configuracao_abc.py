from django import forms

from apps.estoque.models import ConfiguracaoAbcEstoque


class ConfiguracaoAbcEstoqueForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoAbcEstoque
        fields = ['multiplicador_minimo', 'multiplicador_maximo', 'dias_cobertura_extra']
        widgets = {
            'multiplicador_minimo': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'multiplicador_maximo': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'dias_cobertura_extra': forms.NumberInput(attrs={'step': '1'}),
        }

    def clean(self):
        dados = super().clean()
        for campo in ('multiplicador_minimo', 'multiplicador_maximo'):
            valor = dados.get(campo)
            if valor is not None and valor <= 0:
                self.add_error(campo, 'Deve ser maior que zero.')
        return dados
