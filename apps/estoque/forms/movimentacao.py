import json
from decimal import Decimal, InvalidOperation

from django import forms

from apps.core.models import Filial
from apps.estoque.models import LoteProduto, MovimentacaoEstoque
from apps.produtos.models import Produto, ProdutoApresentacao


QUANTIDADE_WIDGET = forms.NumberInput(attrs={
    'step': '0.001',
    'inputmode': 'decimal',
    'data-decimal-places': '3',
    'data-fractional-stock': '1',
})


def _validar_lote_produto(cleaned_data):
    produto = cleaned_data.get('produto')
    lote = cleaned_data.get('lote')
    if produto and produto.controla_lote and not lote:
        raise forms.ValidationError('Informe o lote para produto com controle de lote.')
    if produto and lote and lote.produto_id != produto.pk:
        raise forms.ValidationError('O lote informado nao pertence ao produto selecionado.')
    return cleaned_data


class AjusteEstoqueForm(forms.Form):
    """Ajuste manual de estoque auditado."""

    produto = forms.ModelChoiceField(queryset=Produto.objects.none(), label='Produto')
    lote = forms.ModelChoiceField(
        queryset=LoteProduto.objects.none(),
        required=False,
        label='Lote',
        help_text='Obrigatorio quando o produto controla lote.',
    )
    apresentacao = forms.ModelChoiceField(
        queryset=ProdutoApresentacao.objects.none(), required=False, label='Apresentação',
        help_text='Opcional: conte "em caixas" em vez de digitar direto na unidade base. '
                   '"Nova quantidade atual" abaixo passa a ser a quantidade NESSA apresentação.',
    )
    quantidade_nova = forms.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=0,
        label='Nova quantidade atual',
        help_text='Informe a quantidade total fisica. O sistema calcula a diferenca.',
        widget=QUANTIDADE_WIDGET,
    )
    justificativa = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        label='Justificativa',
        help_text='Obrigatoria. Sera registrada no historico de estoque.',
    )

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial:
            self.fields['produto'].queryset = Produto.objects.for_filial(filial).filter(
                ativo=True,
            ).order_by('descricao')
            self.fields['lote'].queryset = LoteProduto.objects.for_filial(filial).filter(
                status=LoteProduto.Status.ATIVO,
            ).select_related('produto')
            # Sem filtro dinamico por produto (mesmo padrao de
            # AdicionarItemEntradaForm em apps/compras) -- todas as
            # apresentacoes ativas da filial ficam disponiveis, e o
            # `clean()` confere que a escolhida pertence ao produto.
            self.fields['apresentacao'].queryset = ProdutoApresentacao.objects.ativas().filter(
                produto__in=Produto.objects.for_filial(filial), permite_estoque=True,
            ).select_related('produto', 'unidade').order_by('produto__descricao', 'fator_conversao')

    def clean(self):
        cleaned = _validar_lote_produto(super().clean())
        produto = cleaned.get('produto')
        apresentacao = cleaned.get('apresentacao')
        if apresentacao and produto and apresentacao.produto_id != produto.pk:
            raise forms.ValidationError(
                f'A apresentação "{apresentacao.descricao}" não pertence ao produto selecionado.'
            )
        return cleaned


class MovimentacaoManualForm(forms.Form):
    """Entrada ou saida manual auditada pelo MovimentacaoService."""

    TIPOS_MANUAIS = (
        (MovimentacaoEstoque.TipoOperacao.ENTRADA, 'Entrada manual'),
        (MovimentacaoEstoque.TipoOperacao.SAIDA, 'Saida manual'),
        (MovimentacaoEstoque.TipoOperacao.AJUSTE_MAIS, 'Ajuste positivo'),
        (MovimentacaoEstoque.TipoOperacao.AJUSTE_MENOS, 'Ajuste negativo'),
        (MovimentacaoEstoque.TipoOperacao.QUEBRA, 'Quebra/perda'),
        (MovimentacaoEstoque.TipoOperacao.USO_PROPRIO, 'Uso proprio'),
        (MovimentacaoEstoque.TipoOperacao.BONIFICACAO, 'Bonificacao'),
    )

    produto = forms.ModelChoiceField(queryset=Produto.objects.none(), label='Produto')
    lote = forms.ModelChoiceField(
        queryset=LoteProduto.objects.none(),
        required=False,
        label='Lote',
        help_text='Obrigatorio quando o produto controla lote.',
    )
    tipo_operacao = forms.ChoiceField(choices=TIPOS_MANUAIS, label='Operacao')
    quantidade = forms.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=0.001,
        label='Quantidade',
        widget=QUANTIDADE_WIDGET,
    )
    valor_unitario = forms.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=0,
        required=False,
        label='Valor unitario de entrada',
        widget=forms.NumberInput(attrs={
            'step': '0.0001',
            'inputmode': 'decimal',
            'data-decimal-places': '4',
        }),
    )
    documento_numero = forms.CharField(max_length=20, required=False, label='Documento')
    observacao = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=True,
        label='Justificativa',
        help_text='Obrigatoria para rastrear a movimentacao manual.',
    )

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        if filial:
            self.fields['produto'].queryset = Produto.objects.for_filial(filial).filter(
                ativo=True,
            ).order_by('descricao')
            self.fields['lote'].queryset = LoteProduto.objects.for_filial(filial).filter(
                status=LoteProduto.Status.ATIVO,
            ).select_related('produto').order_by('produto__descricao', 'data_validade')

    def clean(self):
        return _validar_lote_produto(super().clean())


class TransferenciaForm(forms.Form):
    """Transferencia de estoque entre filiais da mesma empresa."""

    produto = forms.ModelChoiceField(
        queryset=Produto.objects.none(),
        label='Produto',
        widget=forms.HiddenInput(),
    )
    lote = forms.ModelChoiceField(
        queryset=LoteProduto.objects.none(),
        required=False,
        label='Lote',
        help_text='Obrigatorio quando o produto controla lote.',
    )
    filial_destino = forms.ModelChoiceField(
        queryset=Filial.objects.none(),
        label='Filial de destino',
    )
    quantidade = forms.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=0.001,
        widget=QUANTIDADE_WIDGET,
    )
    observacao = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}),
        required=True,
        label='Justificativa',
        help_text='Obrigatoria para rastrear a transferencia.',
    )

    def __init__(self, *args, filial=None, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['lote'].widget.attrs.update({'class': 'form-input'})
        self.fields['filial_destino'].widget.attrs.update({'class': 'form-input'})
        self.fields['quantidade'].widget.attrs.update({
            'class': 'form-input',
            'inputmode': 'decimal',
            'placeholder': '0,000',
        })
        self.fields['observacao'].widget.attrs.update({
            'class': 'form-input',
            'placeholder': 'Informe o motivo da transferencia',
        })
        if filial:
            self.fields['produto'].queryset = Produto.objects.for_filial(filial).filter(
                ativo=True,
            ).order_by('descricao')
            self.fields['lote'].queryset = LoteProduto.objects.for_filial(filial).filter(
                status=LoteProduto.Status.ATIVO,
            )
        if empresa and filial:
            self.fields['filial_destino'].queryset = Filial.objects.filter(
                empresa=empresa,
                ativo=True,
            ).exclude(pk=filial.pk)

    def clean(self):
        return _validar_lote_produto(super().clean())


class TransferenciaMultiplaForm(forms.Form):
    """Documento de transferencia com uma ou mais linhas de produto/apresentacao."""

    filial_destino = forms.ModelChoiceField(
        queryset=Filial.objects.none(),
        label='Filial de destino',
    )
    itens_json = forms.CharField(widget=forms.HiddenInput())
    observacao = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=True,
        label='Justificativa',
    )

    def __init__(self, *args, filial=None, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        self.fields['filial_destino'].widget.attrs.update({'class': 'form-input'})
        self.fields['observacao'].widget.attrs.update({
            'class': 'form-input',
            'placeholder': 'Informe o motivo da transferencia',
        })
        if empresa and filial:
            self.fields['filial_destino'].queryset = Filial.objects.filter(
                empresa=empresa,
                ativo=True,
            ).exclude(pk=filial.pk)

    def clean_itens_json(self):
        valor = self.cleaned_data['itens_json']
        try:
            itens = json.loads(valor)
        except (TypeError, ValueError):
            raise forms.ValidationError('A lista de produtos e invalida.')
        if not isinstance(itens, list) or not itens:
            raise forms.ValidationError('Adicione pelo menos um produto a transferencia.')
        if len(itens) > 100:
            raise forms.ValidationError('A transferencia aceita no maximo 100 itens por vez.')
        return itens

    def clean(self):
        cleaned = super().clean()
        itens = cleaned.get('itens_json')
        destino = cleaned.get('filial_destino')
        if not itens or not self.filial:
            return cleaned

        produtos_ids = set()
        apresentacoes_ids = set()
        lotes_ids = set()
        for indice, item in enumerate(itens, start=1):
            if not isinstance(item, dict):
                raise forms.ValidationError(f'Item {indice}: formato invalido.')
            try:
                produtos_ids.add(int(item.get('produto_id')))
                if item.get('apresentacao_id'):
                    apresentacoes_ids.add(int(item['apresentacao_id']))
                if item.get('lote_id'):
                    lotes_ids.add(int(item['lote_id']))
            except (TypeError, ValueError):
                raise forms.ValidationError(f'Item {indice}: produto, apresentacao ou lote invalido.')

        produtos = {
            produto.pk: produto
            for produto in Produto.objects.for_filial(self.filial).filter(
                pk__in=produtos_ids,
                ativo=True,
            ).select_related('unidade_medida')
        }
        apresentacoes = {
            apresentacao.pk: apresentacao
            for apresentacao in ProdutoApresentacao.objects.for_filial(self.filial).filter(
                pk__in=apresentacoes_ids,
                permite_estoque=True,
                fator_conversao__gt=0,
            ).select_related('unidade')
        }
        lotes = {
            lote.pk: lote
            for lote in LoteProduto.objects.for_filial(self.filial).filter(
                pk__in=lotes_ids,
                status=LoteProduto.Status.ATIVO,
            )
        }

        normalizados = []
        for indice, item in enumerate(itens, start=1):
            produto = produtos.get(int(item['produto_id']))
            if not produto:
                raise forms.ValidationError(f'Item {indice}: produto nao esta ativo na filial de origem.')
            try:
                quantidade_comercial = Decimal(str(item.get('quantidade', '')).replace(',', '.'))
            except (InvalidOperation, TypeError, ValueError):
                raise forms.ValidationError(f'Item {indice}: informe uma quantidade valida.')
            if quantidade_comercial <= 0:
                raise forms.ValidationError(f'Item {indice}: a quantidade deve ser positiva.')

            apresentacao = None
            fator = Decimal('1')
            if item.get('apresentacao_id'):
                apresentacao = apresentacoes.get(int(item['apresentacao_id']))
                if not apresentacao or apresentacao.produto_id != produto.pk:
                    raise forms.ValidationError(
                        f'Item {indice}: apresentacao nao pertence ao produto ou nao permite estoque.'
                    )
                if destino and not apresentacao.filiais_vinculo.filter(
                    filial=destino,
                    ativo=True,
                ).exists():
                    raise forms.ValidationError(
                        f'Item {indice}: a apresentacao "{apresentacao.descricao}" nao esta ativa na filial de destino.'
                    )
                fator = apresentacao.fator_conversao

            lote = None
            if item.get('lote_id'):
                lote = lotes.get(int(item['lote_id']))
                if not lote or lote.produto_id != produto.pk:
                    raise forms.ValidationError(f'Item {indice}: lote nao pertence ao produto selecionado.')
            if produto.controla_lote and not lote:
                raise forms.ValidationError(
                    f'Item {indice}: informe o lote de "{produto.descricao}".'
                )

            quantidade_base = quantidade_comercial * fator
            if quantidade_base > Decimal('999999999.999'):
                raise forms.ValidationError(f'Item {indice}: quantidade excede o limite permitido.')
            normalizados.append({
                'produto': produto,
                'apresentacao': apresentacao,
                'lote': lote,
                'quantidade_comercial': quantidade_comercial,
                'fator_conversao': fator,
                'quantidade_base': quantidade_base,
            })
        cleaned['itens'] = normalizados
        return cleaned
