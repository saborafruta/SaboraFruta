"""A meta de produção do dia."""
from django import forms

from apps.polpa.models import MetaProducao

ENTRADA = {'class': 'form-input w-full'}


class MetaForm(forms.ModelForm):
    class Meta:
        model = MetaProducao
        fields = ('data', 'meta_kg', 'observacao')
        widgets = {
            'data': forms.DateInput(attrs={**ENTRADA, 'type': 'date'}),
            'meta_kg': forms.NumberInput(attrs={**ENTRADA, 'step': '0.001'}),
            'observacao': forms.TextInput(attrs=ENTRADA),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        self.fields['data'].required = False
        self.fields['data'].help_text = (
            'Vazio = meta padrão, que vale todo dia sem meta própria.'
        )
        self.fields['observacao'].required = False

    def clean(self):
        dados = super().clean()
        data = dados.get('data')
        # UMA META POR DIA. O banco também barra, mas o erro dele é uma
        # página de servidor — e quem cadastra precisa saber que já existe
        # uma, para EDITAR em vez de tentar criar outra.
        existentes = MetaProducao.objects.for_filial(self.filial).filter(data=data)
        if self.instance.pk:
            existentes = existentes.exclude(pk=self.instance.pk)
        if existentes.exists():
            alvo = f'para {data:%d/%m/%Y}' if data else 'padrão'
            self.add_error(
                'data',
                f'Já existe uma meta {alvo} — edite a que existe em vez de '
                'criar outra.',
            )
        return dados

    def save(self, commit=True):
        meta = super().save(commit=False)
        if self.filial and not meta.filial_id:
            meta.filial = self.filial
        if commit:
            meta.save()
        return meta
