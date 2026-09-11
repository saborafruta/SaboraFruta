from django import forms

from apps.estoque.models import Deposito, Inventario, ItemInventario


class InventarioForm(forms.ModelForm):
    class Meta:
        model = Inventario
        fields = ['descricao', 'deposito', 'bloquear_movimentacoes', 'observacao']
        widgets = {
            'observacao': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Deposito.objects.filter(ativo=True)
        if filial is not None:
            qs = qs.filter(filial=filial)
        self.fields['deposito'].queryset = qs.order_by('-is_padrao', 'nome')
        self.fields['deposito'].empty_label = None
        if filial is not None and not self.data.get('deposito'):
            self.fields['deposito'].initial = Deposito.padrao_id(filial.pk)


class ItemInventarioForm(forms.ModelForm):
    class Meta:
        model = ItemInventario
        fields = ['quantidade_contada', 'justificativa']
        widgets = {
            'justificativa': forms.Textarea(attrs={'rows': 2}),
        }
