from django import forms

from apps.estoque.models import FaixaCoberturaEstoque
from apps.produtos.models import CategoriaProduto, Produto


class FaixaCoberturaEstoqueForm(forms.ModelForm):
    class Meta:
        model = FaixaCoberturaEstoque
        fields = ['categoria', 'produto', 'dias_critico', 'dias_baixo', 'dias_normal', 'dias_alto']

    def __init__(self, *args, empresa=None, **kwargs):
        self.empresa = empresa
        super().__init__(*args, **kwargs)
        self.fields['categoria'].queryset = CategoriaProduto.objects.filter(empresa=empresa, ativo=True).order_by('nome')
        self.fields['categoria'].required = False
        self.fields['produto'].queryset = Produto.objects.for_empresa(empresa).filter(ativo=True).order_by('descricao')
        self.fields['produto'].required = False

    def clean(self):
        dados = super().clean()
        categoria = dados.get('categoria')
        produto = dados.get('produto')
        if categoria and produto:
            raise forms.ValidationError('Escolha produto ou categoria, não os dois -- deixe o outro em branco para usar o padrão da empresa.')
        limites = [dados.get(campo) for campo in ('dias_critico', 'dias_baixo', 'dias_normal', 'dias_alto')]
        if all(limite is not None for limite in limites) and limites != sorted(limites):
            raise forms.ValidationError('Os limites devem crescer: crítico < baixo < normal < alto.')
        qs = FaixaCoberturaEstoque.objects.filter(empresa=self.empresa, categoria=categoria, produto=produto)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            if produto:
                raise forms.ValidationError('Já existe uma faixa cadastrada para este produto.')
            if categoria:
                raise forms.ValidationError('Já existe uma faixa cadastrada para esta categoria.')
            raise forms.ValidationError('Já existe uma faixa padrão cadastrada para a empresa.')
        return dados

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.empresa = self.empresa
        if commit:
            instance.save()
        return instance
