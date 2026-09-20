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


class AviamentoRapidoForm(forms.Form):
    """
    Cadastro rápido de aviamento dentro da tela do depósito.

    Grava o catálogo (`moda.Aviamento`) já ligado a um produto de estoque
    enxuto e, se vier quantidade, lança o saldo inicial NESTE depósito —
    o mesmo que "Cadastro de Aviamentos › Novo produto de estoque" faria
    em três telas. A unidade sai das unidades da empresa (é o que o
    produto exige); o `Aviamento.unidade` é derivado dela.
    """

    nome = forms.CharField(max_length=80, label='Nome')
    tipo = forms.ChoiceField(label='Tipo')
    unidade_medida = forms.ModelChoiceField(queryset=None, label='Unidade')
    codigo = forms.CharField(max_length=30, required=False, label='Código')
    quantidade_inicial = forms.DecimalField(
        max_digits=12, decimal_places=3, required=False, min_value=0,
        label='Saldo inicial',
    )

    def __init__(self, *args, filial=None, empresa=None, **kwargs):
        from apps.moda.models import Aviamento
        from apps.produtos.models import UnidadeMedida

        self.filial = filial
        super().__init__(*args, **kwargs)
        self.fields['tipo'].choices = [('', 'Tipo'), *Aviamento.Tipo.choices]
        self.fields['unidade_medida'].queryset = (
            UnidadeMedida.objects.filter(empresa=empresa).order_by('sigla')
            if empresa else UnidadeMedida.objects.none()
        )
        self.fields['unidade_medida'].empty_label = 'Unidade'
        self.fields['nome'].widget.attrs['placeholder'] = 'Ex.: Zíper nylon nº 5 preto'
        self.fields['codigo'].widget.attrs['placeholder'] = 'Opcional'
        self.fields['quantidade_inicial'].widget = forms.TextInput(
            attrs={'inputmode': 'decimal', 'placeholder': '0'},
        )
        for campo in self.fields.values():
            css = campo.widget.attrs.get('class', '')
            if 'form-input' not in css:
                campo.widget.attrs['class'] = (css + ' form-input').strip()

    def clean_nome(self):
        from apps.moda.models import Aviamento

        nome = (self.cleaned_data['nome'] or '').strip()
        if not nome:
            raise forms.ValidationError('Informe o nome.')
        if Aviamento.all_objects.filter(filial=self.filial, nome__iexact=nome).exists():
            raise forms.ValidationError(f'Já existe "{nome}" cadastrado nesta filial.')
        return nome

    def unidade_do_aviamento(self):
        """Sigla da unidade de estoque traduzida para as do catálogo."""
        from apps.moda.models import Aviamento

        sigla = self.cleaned_data['unidade_medida'].sigla.strip().lower()
        sigla = {'pç': 'pc', 'peça': 'pc', 'mt': 'm'}.get(sigla, sigla)
        validas = {valor for valor, _ in Aviamento.Unidade.choices}
        return sigla if sigla in validas else Aviamento.Unidade.UNIDADE


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
