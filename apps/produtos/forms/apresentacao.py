from django import forms

from apps.produtos.forms.produto import _aceitar_decimal_br
from apps.produtos.models import ProdutoApresentacao, UnidadeMedida


class ProdutoApresentacaoForm(forms.ModelForm):
    class Meta:
        model = ProdutoApresentacao
        fields = [
            'descricao', 'unidade', 'codigo', 'fator_conversao',
            'preco_venda', 'preco_minimo',
            'permite_venda', 'permite_compra', 'permite_estoque',
            'principal_venda', 'principal_compra', 'ativo',
        ]
        labels = {
            'descricao': 'Descrição',
            'unidade': 'Unidade',
            'codigo': 'Código interno',
            'fator_conversao': 'Fator de conversão',
            'preco_venda': 'Preço de venda',
            'preco_minimo': 'Preço mínimo',
            'permite_venda': 'Permite venda',
            'permite_compra': 'Permite compra',
            'permite_estoque': 'Permite movimentação de estoque',
            'principal_venda': 'Apresentação padrão na venda',
            'principal_compra': 'Apresentação padrão na compra',
            'ativo': 'Ativa',
        }
        help_texts = {
            'fator_conversao': 'Quantidade de unidades base contida nesta apresentação. '
                                'Ex: "Caixa 1.000" = 1000. Sempre direto para a unidade base do produto.',
        }

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields['unidade'].queryset = UnidadeMedida.objects.filter(
                empresa=empresa,
            ).order_by('sigla')
        for name, field in self.fields.items():
            if isinstance(field, forms.DecimalField):
                _aceitar_decimal_br(field)
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault('class', 'product-checkbox')
            else:
                field.widget.attrs.setdefault('class', 'product-input')
