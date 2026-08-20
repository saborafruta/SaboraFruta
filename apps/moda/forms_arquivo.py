"""Anexos do pedido — arte, referência e documentos."""
from django import forms

from .models import ArquivoPedido


class ArquivoPedidoForm(forms.ModelForm):
    """Um arquivo do pedido. O upload em si aceita vários de uma vez."""

    class Meta:
        model = ArquivoPedido
        fields = ['arquivo', 'tipo', 'descricao']
        widgets = {
            'descricao': forms.TextInput(attrs={
                'placeholder': 'Ex.: escudo em curva, planilha de nomes',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # O arquivo não é obrigatório NO FORM porque a view valida a lista
        # inteira antes: o campo é `multiple`, e o form vê um por vez.
        self.fields['arquivo'].required = False
        self.fields['descricao'].required = False
        for campo in self.fields.values():
            classes = campo.widget.attrs.get('class', '')
            campo.widget.attrs['class'] = (classes + ' form-input w-full').strip()
