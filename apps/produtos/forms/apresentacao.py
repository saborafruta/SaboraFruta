from django import forms

from apps.produtos.forms.produto import _aceitar_decimal_br
from apps.produtos.models import ProdutoApresentacao, UnidadeMedida
from apps.produtos.services.codigo_barras_service import (
    codigo_barras_apresentacao_em_uso,
    salvar_codigo_barras_apresentacao,
)


class ProdutoApresentacaoForm(forms.ModelForm):
    codigo_barras = forms.CharField(
        label='Código de barras',
        required=True,
        max_length=32,
        help_text='Código exclusivo desta apresentação. Pode ser informado ou gerado automaticamente.',
    )

    class Meta:
        model = ProdutoApresentacao
        fields = [
            'descricao', 'unidade', 'codigo', 'codigo_barras', 'fator_conversao',
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

    def __init__(self, *args, empresa=None, produto=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.produto = produto or getattr(self.instance, 'produto', None)
        self.fields['codigo_barras'].widget.attrs['id'] = 'id_apresentacao_codigo_barras'
        self._codigo_barras_instance = None
        if getattr(self.instance, 'pk', None):
            self._codigo_barras_instance = (
                self.instance.codigos_barras.filter(ativo=True).order_by('pk').first()
            )
            if self._codigo_barras_instance:
                self.fields['codigo_barras'].initial = self._codigo_barras_instance.ean
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

    def clean_codigo_barras(self):
        codigo = str(self.cleaned_data.get('codigo_barras') or '').strip()
        if not codigo:
            raise forms.ValidationError('Informe ou gere o código de barras desta apresentação.')

        return codigo

    def clean(self):
        cleaned = super().clean()
        codigo = str(cleaned.get('codigo_barras') or '').strip()
        produto = self.produto
        fator = cleaned.get('fator_conversao')
        if codigo and produto and produto.pk:
            if codigo_barras_apresentacao_em_uso(
                codigo,
                produto=produto,
                registro_id=getattr(self._codigo_barras_instance, 'pk', None),
                permitir_codigo_produto=fator == 1,
            ):
                self.add_error('codigo_barras', 'Este código de barras já está em uso.')
        return cleaned

    def salvar_codigo_barras(self, apresentacao):
        """Persiste o EAN informado usando a tabela canônica de códigos.

        O formulário expõe um código principal por apresentação, mas o model
        continua permitindo códigos adicionais no futuro.
        """
        return salvar_codigo_barras_apresentacao(
            apresentacao, self.cleaned_data['codigo_barras'],
        )
