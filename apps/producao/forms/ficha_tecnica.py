from django import forms
from django.db.models import Q
from django.forms import BaseInlineFormSet, inlineformset_factory

from apps.produtos.models import Produto
from apps.producao.models import FichaTecnica, ItemFichaTecnica


def _rotulo_produto(produto):
    unidade = produto.unidade_medida.sigla if produto.unidade_medida_id else '-'
    return f'{produto.descricao} — {unidade} ({produto.get_finalidade_display()})'


class FichaTecnicaForm(forms.ModelForm):
    class Meta:
        model = FichaTecnica
        exclude = ['filial', 'created_at', 'updated_at']
        labels = {
            'produto_acabado': 'Produto que será produzido',
            'quantidade_produzida': 'Rendimento da fórmula',
        }
        widgets = {
            'observacao': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial:
            atual_id = getattr(self.instance, 'produto_acabado_id', None)
            qs = Produto.objects.for_filial(filial).filter(ativo=True)
            if atual_id:
                qs = Produto.objects.for_filial(filial).filter(
                    Q(pk=atual_id) | Q(ativo=True)
                )
            self.fields['produto_acabado'].queryset = qs.select_related(
                'unidade_medida',
            ).order_by('descricao')
            self.fields['produto_acabado'].label_from_instance = _rotulo_produto
        self.fields['quantidade_produzida'].min_value = 0.001
        self.fields['quantidade_produzida'].help_text = (
            'Quantidade de produto acabado gerada por uma execução da fórmula. '
            'Ex.: 1 UN para um pacote de 800 g.'
        )


class ItemFichaTecnicaForm(forms.ModelForm):
    class Meta:
        model = ItemFichaTecnica
        fields = ['materia_prima', 'quantidade', 'perda_prevista', 'observacao']
        labels = {
            'materia_prima': 'Insumo / embalagem',
            'quantidade': 'Quantidade na unidade do insumo',
            'perda_prevista': 'Perda prevista (%)',
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial:
            atual_id = getattr(self.instance, 'materia_prima_id', None)
            qs = Produto.objects.for_filial(filial).filter(ativo=True).exclude(
                tipo_produto=Produto.TipoProduto.SERVICO,
            )
            if atual_id:
                qs = Produto.objects.for_filial(filial).filter(
                    Q(pk=atual_id) | Q(ativo=True)
                ).exclude(tipo_produto=Produto.TipoProduto.SERVICO)
            self.fields['materia_prima'].queryset = qs.select_related(
                'unidade_medida',
            ).order_by('finalidade', 'descricao')
            self.fields['materia_prima'].label_from_instance = _rotulo_produto
        self.fields['quantidade'].min_value = 0.0001
        self.fields['perda_prevista'].min_value = 0
        self.fields['perda_prevista'].max_value = 100


class BaseItemFichaTecnicaFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        produto_acabado_id = getattr(self.instance, 'produto_acabado_id', None)
        itens = []
        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or form.cleaned_data.get('DELETE'):
                continue
            materia_prima = form.cleaned_data.get('materia_prima')
            if materia_prima:
                itens.append(materia_prima.pk)
                if produto_acabado_id and materia_prima.pk == produto_acabado_id:
                    form.add_error(
                        'materia_prima',
                        'O produto acabado não pode ser insumo da própria fórmula.',
                    )
        if not itens:
            raise forms.ValidationError('Informe pelo menos um insumo ou embalagem.')


ItemFichaTecnicaFormSet = inlineformset_factory(
    FichaTecnica,
    ItemFichaTecnica,
    form=ItemFichaTecnicaForm,
    formset=BaseItemFichaTecnicaFormSet,
    extra=1,
    can_delete=True,
)
