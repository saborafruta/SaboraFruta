from django import forms

from apps.cadastros.models import Cliente, Representante, Transportadora
from apps.produtos.models import Produto, TabelaPreco
from apps.vendas.models import DevolucaoVenda, PedidoVenda


class PedidoVendaForm(forms.ModelForm):
    class Meta:
        model = PedidoVenda
        fields = [
            'cliente', 'representante', 'tipo', 'tabela_preco',
            'transportadora', 'modalidade_frete', 'valor_frete',
            'data_entrega_prevista', 'prioridade', 'origem',
            'observacao', 'observacao_interna',
        ]
        widgets = {
            'observacao': forms.Textarea(attrs={'rows': 2}),
            'observacao_interna': forms.Textarea(attrs={'rows': 2}),
            'data_entrega_prevista': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial:
            self.fields['cliente'].queryset = Cliente.objects.for_filial(filial).filter(
                ativo=True, bloqueado=False,
            ).order_by('razao_social')
            self.fields['representante'].queryset = Representante.objects.for_filial(filial).filter(
                ativo=True,
            )
            self.fields['tabela_preco'].queryset = TabelaPreco.objects.for_filial(filial).filter(
                ativo=True,
            )
            self.fields['transportadora'].queryset = Transportadora.objects.for_filial(filial).filter(
                ativo=True,
            )

    def clean(self):
        cleaned_data = super().clean()
        cliente = cleaned_data.get('cliente')
        if cliente and cliente.tabela_preco_id:
            cleaned_data['tabela_preco'] = cliente.tabela_preco
        return cleaned_data


class AdicionarItemForm(forms.Form):
    produto = forms.ModelChoiceField(queryset=Produto.objects.none(), label='Produto')
    quantidade = forms.DecimalField(max_digits=12, decimal_places=3, min_value=0.001)
    valor_unitario = forms.DecimalField(
        max_digits=14, decimal_places=4, required=False,
        help_text='Deixe em branco para usar preço automático (tabela de preço).',
    )
    percentual_desconto = forms.DecimalField(
        max_digits=5, decimal_places=2, min_value=0, max_value=100,
        initial=0, required=False, label='Desconto (%)',
    )

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial:
            self.fields['produto'].queryset = Produto.objects.for_filial(filial).filter(
                ativo=True,
            ).order_by('descricao')


class CancelarPedidoForm(forms.Form):
    motivo = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        label='Motivo do cancelamento',
    )


class DevolverPedidoForm(forms.Form):
    motivo = forms.ChoiceField(choices=DevolucaoVenda.Motivo.choices)
    descricao = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}), required=False,
    )
