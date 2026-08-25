"""
O formulário do item da fábrica — um só para os dois cadastros.

É UM FORMULÁRIO COMUM, e não um `ModelForm`, porque ele grava DOIS modelos:
o `produtos.Produto` do ERP e a `FichaProduto` da fábrica. Um `ModelForm` de
um deles esconderia o outro, e a pessoa cadastraria produto sem ficha — que
é o item que some de todas as telas do vertical.

OS CAMPOS MUDAM COM A CLASSE, e a tela mostra só os que fazem sentido:
validade e código de barras para acabado, quantidade por caixa para
embalagem. Pedir tudo de todo mundo é o que faz alguém digitar zero para o
formulário fechar.
"""
from django import forms

from apps.polpa.models import FichaProduto, Fruta
from apps.produtos.models import UnidadeMedida

ENTRADA = {'class': 'form-input w-full'}
SELECT = {'class': 'form-select w-full'}
C = FichaProduto.Classe


class ItemCatalogoForm(forms.Form):
    """Cadastro de matéria-prima, embalagem ou produto acabado."""

    # ── O que é ──────────────────────────────────────────────────────────
    tipo = forms.ChoiceField(
        label='O que é este item', choices=FichaProduto.Tipo.choices,
        widget=forms.Select(attrs=SELECT),
    )
    descricao = forms.CharField(
        label='Descrição', max_length=150,
        widget=forms.TextInput(attrs={**ENTRADA, 'placeholder': 'Polpa de manga 100 g'}),
    )
    codigo = forms.CharField(
        label='Código interno', max_length=30, required=False,
        widget=forms.TextInput(attrs=ENTRADA),
    )
    sabor = forms.CharField(
        label='Sabor', max_length=60, required=False,
        widget=forms.TextInput(attrs={**ENTRADA, 'placeholder': 'Manga'}),
    )
    fruta = forms.ModelChoiceField(
        label='Fruta', queryset=Fruta.objects.none(), required=False,
        widget=forms.Select(attrs=SELECT),
        help_text='Liga o item à ficha de recebimento daquela fruta.',
    )
    unidade_medida = forms.ModelChoiceField(
        label='Unidade', queryset=UnidadeMedida.objects.none(),
        widget=forms.Select(attrs=SELECT),
    )

    # ── Embalagem e medidas ──────────────────────────────────────────────
    volume_ml = forms.DecimalField(
        label='Volume (ml)', required=False, min_value=0,
        widget=forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
    )
    peso_liquido = forms.DecimalField(
        label='Peso líquido (kg)', required=False, min_value=0,
        widget=forms.NumberInput(attrs={**ENTRADA, 'step': '0.001'}),
    )
    peso_bruto = forms.DecimalField(
        label='Peso bruto (kg)', required=False, min_value=0,
        widget=forms.NumberInput(attrs={**ENTRADA, 'step': '0.001'}),
    )
    tipo_embalagem = forms.CharField(
        label='Embalagem', max_length=60, required=False,
        widget=forms.TextInput(attrs={**ENTRADA, 'placeholder': 'Saco laminado, pote 500 ml'}),
    )
    quantidade_por_embalagem = forms.DecimalField(
        label='Quantidade por caixa', required=False, min_value=0,
        widget=forms.NumberInput(attrs={**ENTRADA, 'step': '0.001'}),
    )
    caixas_por_pallet = forms.IntegerField(
        label='Caixas por pallet', required=False, min_value=0,
        widget=forms.NumberInput(attrs=ENTRADA),
    )

    # ── Conservação ──────────────────────────────────────────────────────
    congelado = forms.BooleanField(label='Congelado', required=False)
    refrigerado = forms.BooleanField(label='Refrigerado', required=False)
    temperatura_minima = forms.DecimalField(
        label='Temperatura mínima (°C)', required=False,
        widget=forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
    )
    temperatura_maxima = forms.DecimalField(
        label='Temperatura máxima (°C)', required=False,
        widget=forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
    )
    validade_dias = forms.IntegerField(
        label='Validade (dias)', required=False, min_value=0,
        widget=forms.NumberInput(attrs={**ENTRADA, 'placeholder': '365'}),
    )

    # ── Fiscal ───────────────────────────────────────────────────────────
    codigo_barras = forms.CharField(
        label='Código de barras', max_length=14, required=False,
        widget=forms.TextInput(attrs=ENTRADA),
    )
    ncm = forms.CharField(
        label='NCM', max_length=8, required=False,
        widget=forms.TextInput(attrs={**ENTRADA, 'placeholder': '20089900'}),
    )
    cest = forms.CharField(
        label='CEST', max_length=7, required=False,
        widget=forms.TextInput(attrs=ENTRADA),
    )
    registro_mapa = forms.CharField(
        label='Registro MAPA/SIF', max_length=40, required=False,
        widget=forms.TextInput(attrs=ENTRADA),
    )

    # ── Preço ────────────────────────────────────────────────────────────
    preco_custo = forms.DecimalField(
        label='Custo', required=False, min_value=0,
        widget=forms.NumberInput(attrs={**ENTRADA, 'step': '0.0001'}),
    )
    preco_venda = forms.DecimalField(
        label='Preço de venda', required=False, min_value=0,
        widget=forms.NumberInput(attrs={**ENTRADA, 'step': '0.0001'}),
    )
    observacao = forms.CharField(
        label='Observação', required=False,
        widget=forms.Textarea(attrs={**ENTRADA, 'rows': 2}),
    )

    def __init__(self, *args, filial=None, ficha=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        self.ficha = ficha

        self.fields['unidade_medida'].queryset = (
            UnidadeMedida.objects.for_filial(filial) if filial
            else UnidadeMedida.objects.none()
        )
        self.fields['fruta'].queryset = (
            Fruta.objects.for_filial(filial).filter(ativo=True) if filial
            else Fruta.objects.none()
        )
        self.fields['fruta'].empty_label = 'Nenhuma'

        if ficha is not None and not self.is_bound:
            self.initial.update(self._do_registro(ficha))

    @staticmethod
    def _do_registro(ficha) -> dict:
        """
        Preenche o formulário a partir dos DOIS modelos.

        Escrito uma vez aqui e não na view: quem edita precisa ver o que já
        está gravado, e um campo esquecido nesta lista some silenciosamente
        do formulário -- e volta vazio para o banco na primeira gravação.
        """
        from apps.produtos.models import Produto

        produto = ficha.produto
        return {
            'tipo': ficha.tipo, 'sabor': ficha.sabor, 'fruta': ficha.fruta_id,
            'volume_ml': ficha.volume_ml, 'validade_dias': ficha.validade_dias,
            'caixas_por_pallet': ficha.caixas_por_pallet,
            'registro_mapa': ficha.registro_mapa, 'observacao': ficha.observacao,
            'descricao': produto.descricao, 'codigo': produto.codigo,
            'codigo_barras': produto.codigo_barras, 'ncm': produto.ncm,
            'cest': produto.cest, 'unidade_medida': produto.unidade_medida_id,
            'peso_liquido': produto.peso_liquido, 'peso_bruto': produto.peso_bruto,
            'tipo_embalagem': produto.tipo_embalagem,
            'quantidade_por_embalagem': produto.quantidade_por_embalagem,
            'temperatura_minima': produto.temperatura_minima,
            'temperatura_maxima': produto.temperatura_maxima,
            'preco_custo': produto.preco_custo, 'preco_venda': produto.preco_venda,
            'congelado': (
                produto.condicao_armazenamento
                == Produto.CondicaoArmazenamento.CONGELADO
            ),
            'refrigerado': (
                produto.condicao_armazenamento
                == Produto.CondicaoArmazenamento.REFRIGERADO
            ),
        }

    def clean(self):
        dados = super().clean()
        tipo = dados.get('tipo')
        dados['classe'] = FichaProduto.CLASSE_DO_TIPO.get(tipo)

        if dados.get('congelado') and dados.get('refrigerado'):
            self.add_error(
                'refrigerado',
                'Um item é congelado ou refrigerado — os dois juntos não '
                'dizem a que temperatura guardar.',
            )

        minima = dados.get('temperatura_minima')
        maxima = dados.get('temperatura_maxima')
        if minima is not None and maxima is not None and minima > maxima:
            self.add_error(
                'temperatura_maxima',
                'A mínima ficou acima da máxima — a faixa não existe.',
            )

        # PESO LÍQUIDO MAIOR QUE O BRUTO é erro de digitação: o bruto inclui
        # a embalagem. Passar batido faria a expedição calcular carga a menos
        # do que o caminhão leva.
        liquido = dados.get('peso_liquido')
        bruto = dados.get('peso_bruto')
        if liquido and bruto and liquido > bruto:
            self.add_error(
                'peso_bruto',
                'O peso bruto ficou menor que o líquido — ele inclui a embalagem.',
            )
        return dados
