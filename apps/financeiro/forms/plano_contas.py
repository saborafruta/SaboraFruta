"""Formularios de Categorias Financeiras."""
from django import forms

from apps.financeiro.models.conta_bancaria import PlanoContas
from apps.financeiro.models.plano_contabil import PlanoContabil

TIPO_CONFIGS = {
    'grupo_receita':    {'tipo': 'R', 'nivel': 1, 'pai_nivel': None},
    'subgrupo_receita': {'tipo': 'R', 'nivel': 2, 'pai_nivel': 1},
    'outras_receitas':  {'tipo': 'R', 'nivel': 3, 'pai_nivel': 2},
    'grupo_despesa':    {'tipo': 'D', 'nivel': 1, 'pai_nivel': None},
    'subgrupo_despesa': {'tipo': 'D', 'nivel': 2, 'pai_nivel': 1},
    'outras_despesas':  {'tipo': 'D', 'nivel': 3, 'pai_nivel': 2},
}


class PlanoContasForm(forms.ModelForm):
    """Criacao / edicao de uma categoria financeira."""

    class Meta:
        model = PlanoContas
        fields = ['conta_pai', 'codigo', 'descricao', 'conta_contabil', 'ativo']
        widgets = {
            'codigo': forms.TextInput(attrs={'placeholder': 'ex.: 1.1.01'}),
            'descricao': forms.TextInput(attrs={'placeholder': 'ex.: Vendas de mercadorias'}),
        }
        labels = {
            'conta_pai': 'Vinculado a',
            'codigo': 'Codigo',
            'descricao': 'Descricao',
            'conta_contabil': 'Conta contábil vinculada',
            'ativo': 'Ativo',
        }

    def __init__(self, *args, empresa=None, tipo_key=None, **kwargs):
        super().__init__(*args, **kwargs)
        cfg = TIPO_CONFIGS.get(tipo_key, {})
        pai_nivel = cfg.get('pai_nivel')
        tipo = cfg.get('tipo')

        if empresa and cfg.get('nivel') == 3:
            self.fields['conta_contabil'].queryset = (
                PlanoContabil.objects
                .filter(
                    empresa=empresa,
                    tipo_conta=PlanoContabil.TipoConta.ANALITICA,
                    ativo=True,
                )
                .order_by('ordem')
            )
            self.fields['conta_contabil'].required = True
            self.fields['conta_contabil'].help_text = (
                'A classificação contábil será preenchida automaticamente nos lançamentos.'
            )
        else:
            self.fields['conta_contabil'].queryset = PlanoContabil.objects.none()
            self.fields['conta_contabil'].required = False
            self.fields['conta_contabil'].widget = forms.HiddenInput()

        # Conta pai: so mostra contas do nivel pai, do mesmo tipo
        if empresa and pai_nivel and tipo:
            self.fields['conta_pai'].queryset = (
                PlanoContas.objects
                .filter(empresa=empresa, tipo=tipo, nivel=pai_nivel, ativo=True)
                .order_by('codigo')
            )
            self.fields['conta_pai'].required = True
            self.fields['conta_pai'].help_text = (
                f'Selecione o grupo pai (nivel {pai_nivel}).'
            )
        else:
            # nivel 1 (grupos) nao tem pai
            self.fields['conta_pai'].queryset = PlanoContas.objects.none()
            self.fields['conta_pai'].required = False
            self.fields['conta_pai'].widget = forms.HiddenInput()

        # Guarda tipo_key para uso no POST
        self.fields['tipo_key'] = forms.CharField(
            widget=forms.HiddenInput(),
            initial=tipo_key or '',
            required=False,
        )


class CategoriaFinanceiraChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.caminho_descricao} ({obj.conta_contabil.classificacao})"
