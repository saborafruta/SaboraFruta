from django import forms

from apps.estoque.models import Deposito


class DepositoForm(forms.ModelForm):
    # Não é campo de model direto: `tipos_material` é JSONField (lista),
    # não mapeia num widget de ModelForm sozinho. Escolhas vêm da moda
    # (import local no __init__, pra não criar dependência de módulo
    # estoque -> moda em tempo de carga) e o valor é lido/gravado à mão
    # em __init__/save().
    tipos_material = forms.MultipleChoiceField(
        required=False, widget=forms.CheckboxSelectMultiple,
        label='Tipos de material (moda) que caem aqui automaticamente',
        help_text=(
            'Ao dar baixa no corte ou reservar material da OP, o tecido/aviamento '
            'vai para o depósito marcado com o tipo dele. Sem nenhum tipo marcado '
            'em nenhum depósito, tudo cai no primeiro depósito de produção.'
        ),
    )

    class Meta:
        model = Deposito
        fields = ['nome', 'tipo', 'permite_venda', 'permite_producao', 'ativo']
        widgets = {
            'nome': forms.TextInput(attrs={'placeholder': 'Ex.: Loja, Fábrica'}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        self.filial = filial
        super().__init__(*args, **kwargs)
        from apps.moda.models import MaterialFicha
        self.fields['tipos_material'].choices = MaterialFicha.Tipo.choices
        if self.instance and self.instance.pk:
            self.initial.setdefault('tipos_material', list(self.instance.tipos_material or []))
        if self.instance and self.instance.pk and self.instance.is_padrao:
            # O depósito padrão não pode ser desativado nem renomeado à toa:
            # é o destino de tudo que não indica depósito.
            self.fields['ativo'].disabled = True
            self.fields['ativo'].help_text = 'O depósito padrão da filial não pode ser desativado.'

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.tipos_material = self.cleaned_data.get('tipos_material') or []
        if commit:
            instance.save()
        return instance

    def clean_nome(self):
        nome = self.cleaned_data['nome'].strip()
        if not nome:
            raise forms.ValidationError('Informe um nome.')
        qs = Deposito.objects.filter(filial=self.filial, nome__iexact=nome)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('Já existe um depósito com esse nome nesta filial.')
        return nome


class TransferenciaInternaForm(forms.Form):
    """Move saldo de um depósito para outro na mesma filial (sem NF-e)."""

    produto = forms.IntegerField(widget=forms.HiddenInput)
    deposito_origem = forms.ModelChoiceField(queryset=Deposito.objects.none(), label='De')
    deposito_destino = forms.ModelChoiceField(queryset=Deposito.objects.none(), label='Para')
    quantidade = forms.DecimalField(
        min_value=0, max_digits=12, decimal_places=3, label='Quantidade',
        widget=forms.NumberInput(attrs={'step': '0.001', 'inputmode': 'decimal'}),
    )
    observacao = forms.CharField(
        required=False, label='Observação',
        widget=forms.TextInput(attrs={'placeholder': 'Opcional'}),
    )

    def __init__(self, *args, filial=None, **kwargs):
        self.filial = filial
        super().__init__(*args, **kwargs)
        depositos = Deposito.objects.filter(filial=filial, ativo=True).order_by('nome')
        self.fields['deposito_origem'].queryset = depositos
        self.fields['deposito_destino'].queryset = depositos

    def clean(self):
        dados = super().clean()
        origem = dados.get('deposito_origem')
        destino = dados.get('deposito_destino')
        if origem and destino and origem == destino:
            self.add_error('deposito_destino', 'Escolha um depósito diferente da origem.')
        return dados
