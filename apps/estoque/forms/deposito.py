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

    Se a empresa ainda não tem a unidade que o aviamento pede (só "UN", por
    exemplo, e o elástico é vendido em metro), o campo aceita `NOVA_UNIDADE`
    e os campos `nova_unidade_*`: a unidade é criada junto, por
    `obter_unidade()`, sem sair da tela.
    """

    NOVA_UNIDADE = '__nova__'
    # Unidades que a lista oferece prontas quando a empresa ainda não as tem:
    # o valor do select é `padrao:<SIGLA>` e a unidade nasce junto com o
    # aviamento, exatamente como em "Criar nova unidade".
    PREFIXO_PADRAO = 'padrao:'
    UNIDADES_PADRAO = {
        'UN': ('Unidade', 'unidade'),
        'M': ('Metro', 'comprimento'),
    }

    nome = forms.CharField(max_length=80, label='Nome')
    tipo = forms.ChoiceField(label='Tipo')
    unidade_medida = forms.ModelChoiceField(queryset=None, label='Unidade')
    nova_unidade_sigla = forms.CharField(max_length=6, required=False, label='Sigla')
    nova_unidade_descricao = forms.CharField(max_length=40, required=False, label='Nome da unidade')
    nova_unidade_tipo = forms.ChoiceField(required=False, label='Tipo de medida')
    codigo = forms.CharField(max_length=30, required=False, label='Código')
    quantidade_inicial = forms.DecimalField(
        max_digits=12, decimal_places=3, required=False, min_value=0,
        label='Saldo inicial',
    )

    def __init__(self, *args, filial=None, empresa=None, **kwargs):
        from apps.moda.models import Aviamento
        from apps.produtos.models import UnidadeMedida

        self.filial = filial
        self.empresa = empresa
        super().__init__(*args, **kwargs)
        # "Nova unidade" não é um pk: tira o marcador dos dados antes de o
        # ModelChoiceField validar e lembra a escolha (para reabrir os
        # campos da unidade nova se o formulário voltar com erro).
        escolha = self.data.get('unidade_medida') or ''
        padrao = self.UNIDADES_PADRAO.get(escolha.removeprefix(self.PREFIXO_PADRAO).upper())             if escolha.startswith(self.PREFIXO_PADRAO) else None
        self.criando_unidade = escolha == self.NOVA_UNIDADE or padrao is not None
        if self.criando_unidade:
            self.data = self.data.copy()
            self.data['unidade_medida'] = ''
            if padrao:
                sigla = escolha.removeprefix(self.PREFIXO_PADRAO).upper()
                self.data['nova_unidade_sigla'] = sigla
                self.data['nova_unidade_descricao'] = padrao[0]
                self.data['nova_unidade_tipo'] = padrao[1]
            self.fields['unidade_medida'].required = False
        self.fields['nova_unidade_tipo'].choices = [
            ('', 'Não informar'), *UnidadeMedida.Tipo.choices,
        ]
        self.fields['tipo'].choices = [('', 'Tipo'), *Aviamento.Tipo.choices]
        self.fields['unidade_medida'].queryset = (
            UnidadeMedida.objects.filter(empresa=empresa).order_by('sigla')
            if empresa else UnidadeMedida.objects.none()
        )
        self.fields['unidade_medida'].label = 'Tipo de unidade'
        self.fields['unidade_medida'].empty_label = 'Unidade'
        existentes = {u.sigla.upper() for u in self.fields['unidade_medida'].queryset}
        # Só oferece a pronta se a empresa ainda não tem a sigla; e o
        # `escolha` que voltou com erro continua marcada na tela.
        self.unidades_padrao_faltando = [
            (f'{self.PREFIXO_PADRAO}{sigla}', sigla, descricao)
            for sigla, (descricao, _tipo) in self.UNIDADES_PADRAO.items()
            if sigla not in existentes
        ]
        self.unidade_escolhida = escolha
        self.fields['nome'].widget.attrs['placeholder'] = 'Ex.: Zíper nylon nº 5 preto'
        self.fields['codigo'].widget.attrs['placeholder'] = 'Opcional'
        self.fields['nova_unidade_sigla'].widget.attrs['placeholder'] = 'Ex.: M'
        self.fields['nova_unidade_descricao'].widget.attrs['placeholder'] = 'Ex.: Metro'
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

    def clean(self):
        from apps.produtos.models import UnidadeMedida

        dados = super().clean()
        if self.criando_unidade:
            sigla = (dados.get('nova_unidade_sigla') or '').strip().upper()
            descricao = (dados.get('nova_unidade_descricao') or '').strip()
            if not sigla:
                self.add_error('nova_unidade_sigla', 'Informe a sigla.')
            elif UnidadeMedida.objects.filter(empresa=self.empresa, sigla__iexact=sigla).exists():
                self.add_error(
                    'nova_unidade_sigla',
                    f'Já existe a unidade "{sigla}". Escolha-a na lista.',
                )
            if not descricao:
                self.add_error('nova_unidade_descricao', 'Informe o nome.')
        elif not dados.get('unidade_medida') and 'unidade_medida' not in self.errors:
            self.add_error('unidade_medida', 'Selecione a unidade.')
        return dados

    def obter_unidade(self):
        """
        A unidade escolhida — ou criada agora, vinculada à filial. Chamar
        dentro da transação do cadastro: se o resto falhar, ela não fica.
        """
        from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

        if not self.criando_unidade:
            return self.cleaned_data['unidade_medida']
        dados = self.cleaned_data
        tipo = dados.get('nova_unidade_tipo') or ''
        unidade = UnidadeMedida.objects.create(
            empresa=self.empresa,
            sigla=dados['nova_unidade_sigla'].strip().upper(),
            descricao=dados['nova_unidade_descricao'].strip(),
            tipo=tipo,
            # Contagem (un, cone, par) não aceita meia peça.
            casas_decimais=0 if tipo == UnidadeMedida.Tipo.UNIDADE else 3,
        )
        UnidadeMedidaFilial.objects.get_or_create(unidade=unidade, filial=self.filial)
        return unidade

    @staticmethod
    def sigla_do_aviamento(unidade):
        """Sigla da unidade de estoque traduzida para as do catálogo."""
        from apps.moda.models import Aviamento

        sigla = unidade.sigla.strip().lower()
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
