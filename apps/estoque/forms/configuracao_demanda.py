from django import forms

from apps.estoque.models import ConfiguracaoDemandaPonderada


class ConfiguracaoDemandaPonderadaForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoDemandaPonderada
        fields = ['peso_7_dias', 'peso_15_dias', 'peso_30_dias', 'peso_60_dias', 'peso_90_dias']

    def clean(self):
        dados = super().clean()
        pesos = [dados.get(campo) for campo in self.Meta.fields]
        if all(p is not None for p in pesos) and sum(pesos) <= 0:
            raise forms.ValidationError('Pelo menos um período precisa ter peso maior que zero.')
        return dados
