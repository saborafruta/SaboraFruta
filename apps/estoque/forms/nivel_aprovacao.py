from django import forms

from apps.estoque.models import NivelAprovacaoTransferencia


class NivelAprovacaoTransferenciaForm(forms.ModelForm):
    class Meta:
        model = NivelAprovacaoTransferencia
        fields = ['nivel_nome', 'valor_minimo', 'valor_maximo']

    def clean(self):
        dados = super().clean()
        minimo, maximo = dados.get('valor_minimo'), dados.get('valor_maximo')
        if minimo is not None and maximo is not None and maximo <= minimo:
            raise forms.ValidationError('O valor máximo deve ser maior que o mínimo.')
        return dados
