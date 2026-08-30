from django import forms
from django.forms import formset_factory

from apps.produtos.models import (
    BrindeProduto,
    CategoriaProduto,
    CondicaoQuantidade,
    DIAS_SEMANA_TODOS,
    KitCategoria,
    KitCategoriaRegra,
    KitProduto,
    KitProdutoItem,
    Produto,
    PromocaoQuantidade,
    PromocaoQuantidadeFaixa,
    TipoDesconto,
)


DIAS_SEMANA_CHOICES = [
    ('0', 'Seg'),
    ('1', 'Ter'),
    ('2', 'Qua'),
    ('3', 'Qui'),
    ('4', 'Sex'),
    ('5', 'Sab'),
    ('6', 'Dom'),
]


class DecimalBRField(forms.DecimalField):
    def __init__(self, *args, **kwargs):
        decimal_places = kwargs.get('decimal_places')
        widget = kwargs.get('widget')
        if widget is None:
            widget = forms.TextInput()
            kwargs['widget'] = widget
        super().__init__(*args, **kwargs)
        if not isinstance(self.widget, forms.HiddenInput):
            self.widget.attrs.setdefault('inputmode', 'decimal')
            if decimal_places is not None:
                self.widget.attrs.setdefault('data-decimal-places', str(decimal_places))

    def to_python(self, value):
        if isinstance(value, str):
            value = value.strip()
            if ',' in value:
                value = value.replace('.', '').replace(',', '.')
        return super().to_python(value)


class ProdutoChoiceMixin:
    def _setup_produtos(self, filial):
        qs = Produto.objects.for_filial(filial).filter(ativo=True).order_by('descricao')
        for name, field in self.fields.items():
            if name.startswith('produto'):
                field.queryset = qs


class BasePromocaoForm(forms.ModelForm):
    data_inicio = forms.DateField(label='Inicio', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    data_fim = forms.DateField(label='Fim', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    dias_semana = forms.MultipleChoiceField(
        label='Dias da semana',
        required=False,
        choices=DIAS_SEMANA_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        initial=DIAS_SEMANA_TODOS.split(','),
    )

    def _style_fields(self):
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({'class': 'promo-checkbox'})
            elif isinstance(field.widget, forms.CheckboxSelectMultiple):
                field.widget.attrs.update({'class': 'promo-weekday-input'})
            else:
                field.widget.attrs.update({'class': 'promo-input'})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        dias = getattr(self.instance, 'dias_semana', '') if self.instance else ''
        self.initial.setdefault('dias_semana', (dias or DIAS_SEMANA_TODOS).split(','))

    def clean_dias_semana(self):
        dias = self.cleaned_data.get('dias_semana') or []
        if not dias:
            raise forms.ValidationError('Selecione ao menos um dia da semana.')
        validos = {valor for valor, _ in DIAS_SEMANA_CHOICES}
        dias = [dia for dia in dias if dia in validos]
        if not dias:
            raise forms.ValidationError('Selecione ao menos um dia da semana.')
        return ','.join(sorted(dias, key=int))

    def clean(self):
        cleaned = super().clean()
        inicio = cleaned.get('data_inicio')
        fim = cleaned.get('data_fim')
        if inicio and fim and inicio > fim:
            raise forms.ValidationError('A data inicial nao pode ser maior que a data final.')
        return cleaned


class PromocaoQuantidadeForm(ProdutoChoiceMixin, BasePromocaoForm):
    class Meta:
        model = PromocaoQuantidade
        fields = ['produto', 'nome', 'data_inicio', 'data_fim', 'dias_semana', 'usar_preco_promocional', 'replicar_filiais', 'ativo']
        labels = {
            'produto': 'Selecione o produto que deseja fazer o combo',
            'nome': 'Nome do combo',
            'usar_preco_promocional': 'Usar preco promocional',
            'replicar_filiais': 'Replicar para filiais',
            'ativo': 'Ativo',
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_produtos(filial)
        self.fields['produto'].widget = forms.HiddenInput()
        self._style_fields()


class PromocaoQuantidadeFaixaForm(forms.Form):
    condicao_quantidade = forms.ChoiceField(
        label='Condicao',
        required=False,
        choices=[
            (CondicaoQuantidade.IGUAL, 'Quantidade'),
            (CondicaoQuantidade.A_PARTIR_DE, 'A partir de'),
        ],
    )
    quantidade_minima = DecimalBRField(label='Quantidade', required=False, max_digits=12, decimal_places=2)
    tipo_desconto = forms.ChoiceField(
        label='Tipo do desconto',
        required=False,
        choices=[
            (TipoDesconto.PERCENTUAL, 'Desconto em %'),
            (TipoDesconto.VALOR, 'Desconto em R$ no total'),
        ],
    )
    valor = DecimalBRField(label='Desconto (% ou R$)', required=False, max_digits=14, decimal_places=2)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'promo-input'})


PromocaoQuantidadeFaixaFormSet = formset_factory(PromocaoQuantidadeFaixaForm, extra=4, min_num=1, validate_min=True)


class KitProdutoForm(BasePromocaoForm):
    valor_desconto = DecimalBRField(label='Desconto (% ou R$)', required=False, max_digits=14, decimal_places=2)
    tipo_desconto = forms.ChoiceField(
        label='Tipo de desconto',
        choices=[
            (TipoDesconto.PERCENTUAL, 'Desconto em %'),
            (TipoDesconto.VALOR, 'Desconto em R$'),
            (TipoDesconto.PRECO_FINAL, 'Definir valor final'),
        ],
    )

    class Meta:
        model = KitProduto
        fields = [
            'nome', 'tipo_desconto', 'valor_desconto', 'data_inicio', 'data_fim', 'dias_semana',
            'permite_preco_promocional', 'replicar_filiais', 'ativo',
        ]
        labels = {
            'nome': 'Nome do kit',
            'tipo_desconto': 'Tipo de desconto',
            'permite_preco_promocional': 'Usar preco promocional',
            'replicar_filiais': 'Replicar para filiais',
            'ativo': 'Ativo',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()

    def clean_valor_desconto(self):
        return self.cleaned_data.get('valor_desconto') or 0


class KitProdutoItemForm(ProdutoChoiceMixin, forms.Form):
    produto = forms.ModelChoiceField(label='Produto', required=False, queryset=Produto.objects.none())
    quantidade = DecimalBRField(label='Quantidade', required=False, max_digits=12, decimal_places=2)

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_produtos(filial)
        self.fields['produto'].widget = forms.HiddenInput()
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'promo-input'})


KitProdutoItemFormSet = formset_factory(KitProdutoItemForm, extra=5, min_num=1, validate_min=True)


class BrindeProdutoForm(ProdutoChoiceMixin, BasePromocaoForm):
    quantidade_gatilho = DecimalBRField(label='Quantidade para ganhar', required=False, max_digits=12, decimal_places=2)

    class Meta:
        model = BrindeProduto
        fields = [
            'nome', 'produto_gatilho', 'quantidade_gatilho', 'data_inicio', 'data_fim', 'dias_semana',
            'permite_preco_promocional', 'replicar_filiais', 'ativo',
        ]
        labels = {
            'nome': 'Nome do brinde',
            'produto_gatilho': 'Produto gerador de brinde',
            'quantidade_gatilho': 'Quantidade para ganhar',
            'permite_preco_promocional': 'Usar preco promocional',
            'replicar_filiais': 'Replicar para filiais',
            'ativo': 'Ativo',
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_produtos(filial)
        self.fields['produto_gatilho'].widget = forms.HiddenInput()
        self.fields['quantidade_gatilho'].initial = self.initial.get('quantidade_gatilho') or 1
        self._style_fields()

    def clean_quantidade_gatilho(self):
        quantidade = self.cleaned_data.get('quantidade_gatilho') or 1
        if quantidade <= 0:
            raise forms.ValidationError('Informe uma quantidade maior que zero.')
        return quantidade


class BrindeProdutoItemForm(ProdutoChoiceMixin, forms.Form):
    produto = forms.ModelChoiceField(label='Produto brinde', required=False, queryset=Produto.objects.none())
    quantidade = DecimalBRField(label='Quantidade', required=False, max_digits=12, decimal_places=2)

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_produtos(filial)
        self.fields['produto'].widget = forms.HiddenInput()
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'promo-input'})


BrindeProdutoItemFormSet = formset_factory(BrindeProdutoItemForm, extra=5, min_num=1, validate_min=True)


class KitCategoriaForm(BasePromocaoForm):
    class Meta:
        model = KitCategoria
        fields = [
            'nome', 'data_inicio', 'data_fim', 'dias_semana',
            'permite_preco_promocional', 'replicar_filiais', 'ativo',
        ]
        labels = {
            'nome': 'Nome do desconto',
            'permite_preco_promocional': 'Usar preco promocional',
            'replicar_filiais': 'Replicar para filiais',
            'ativo': 'Ativo',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class KitCategoriaRegraForm(forms.Form):
    categoria = forms.ModelChoiceField(label='Categoria', required=False, queryset=CategoriaProduto.objects.none(), empty_label='Todas as categorias')
    subcategoria = forms.ModelChoiceField(label='Subcategoria', required=False, queryset=CategoriaProduto.objects.none())
    quantidade_minima = DecimalBRField(label='Quantidade', required=False, max_digits=12, decimal_places=2)
    tipo_desconto = forms.ChoiceField(
        label='Tipo de desconto',
        required=False,
        choices=[
            (TipoDesconto.PERCENTUAL, 'Desconto em %'),
            (TipoDesconto.VALOR, 'Desconto em R$'),
        ],
    )
    valor_desconto = DecimalBRField(label='Desconto (% ou R$)', required=False, max_digits=14, decimal_places=2)

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        categorias = CategoriaProduto.objects.for_filial(filial).filter(ativo=True).order_by('categoria_pai__nome', 'nome')
        self.fields['categoria'].queryset = categorias.filter(categoria_pai__isnull=True)
        self.fields['subcategoria'].queryset = categorias.filter(categoria_pai__isnull=False)
        self.fields['quantidade_minima'].initial = 1
        self.fields['quantidade_minima'].widget = forms.HiddenInput()
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'promo-input'})

    def clean(self):
        cleaned = super().clean()
        categoria = cleaned.get('categoria')
        subcategoria = cleaned.get('subcategoria')
        if subcategoria and categoria and subcategoria.categoria_pai_id != categoria.pk:
            self.add_error('subcategoria', 'Subcategoria nao pertence a categoria.')
        return cleaned


KitCategoriaRegraFormSet = formset_factory(KitCategoriaRegraForm, extra=3, min_num=1, validate_min=True)


class PrecoPromocionalItemForm(ProdutoChoiceMixin, forms.Form):
    produto = forms.ModelChoiceField(label='Produto', required=False, queryset=Produto.objects.none())
    promocao_tipo_desconto = forms.ChoiceField(
        choices=(
            ('preco_final', 'Preco final'),
            ('percentual', 'Percentual'),
            ('valor', 'Valor em R$'),
        ),
        required=False,
        initial='preco_final',
        widget=forms.HiddenInput(),
    )
    promocao_valor_desconto = DecimalBRField(
        label='Regra do desconto',
        required=False,
        max_digits=14,
        decimal_places=2,
        widget=forms.HiddenInput(),
    )
    preco_promocional = DecimalBRField(label='Preco promocional', required=False, max_digits=14, decimal_places=2)
    promocao_inicio = forms.DateField(label='Inicio', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    promocao_fim = forms.DateField(label='Fim', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    promocao_dias_semana = forms.MultipleChoiceField(
        label='Dias da semana',
        required=False,
        choices=DIAS_SEMANA_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        initial=DIAS_SEMANA_TODOS.split(','),
    )
    ativo = forms.BooleanField(label='Ativo', required=False, initial=True)

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_produtos(filial)
        self.fields['produto'].widget = forms.HiddenInput()
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({'class': 'promo-checkbox'})
            elif isinstance(field.widget, forms.CheckboxSelectMultiple):
                field.widget.attrs.update({'class': 'promo-weekday-input'})
            elif isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs.update({'data-product-id': ''})
            else:
                field.widget.attrs.update({'class': 'promo-input'})

    def clean_promocao_dias_semana(self):
        dias = self.cleaned_data.get('promocao_dias_semana') or []
        if not dias:
            return DIAS_SEMANA_TODOS
        validos = {valor for valor, _ in DIAS_SEMANA_CHOICES}
        dias = [dia for dia in dias if dia in validos]
        if not dias:
            return DIAS_SEMANA_TODOS
        return ','.join(sorted(dias, key=int))

    def clean(self):
        cleaned = super().clean()
        produto = cleaned.get('produto')
        preco_promocional = cleaned.get('preco_promocional')
        tipo = cleaned.get('promocao_tipo_desconto') or 'preco_final'
        valor_desconto = cleaned.get('promocao_valor_desconto')
        inicio = cleaned.get('promocao_inicio')
        fim = cleaned.get('promocao_fim')
        if produto and preco_promocional is None:
            self.add_error('preco_promocional', 'Informe o preco promocional.')
        if produto and valor_desconto is None:
            cleaned['promocao_valor_desconto'] = preco_promocional
        if tipo in ('percentual', 'valor') and valor_desconto is not None and valor_desconto < 0:
            self.add_error('promocao_valor_desconto', 'O desconto nao pode ser negativo.')
        if preco_promocional is not None and preco_promocional <= 0:
            self.add_error('preco_promocional', 'O preco promocional deve ser maior que zero.')
        if inicio and fim and inicio > fim:
            raise forms.ValidationError('A data inicial nao pode ser maior que a data final.')
        return cleaned


PrecoPromocionalItemFormSet = formset_factory(PrecoPromocionalItemForm, extra=5, min_num=1, validate_min=True)
