"""
Formulários da receita: o cabeçalho, os ingredientes e as etapas.

O CABEÇALHO GRAVA DOIS MODELOS (a ficha técnica do ERP e a receita da
fábrica), pelo mesmo motivo do catálogo: separar em duas telas daria ficha
sem receita — e ficha sem rendimento esperado é uma produção sem régua.
"""
from django import forms

from apps.polpa.models import EtapaReceita, FichaProduto
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import Produto

ENTRADA = {'class': 'form-input w-full'}
SELECT = {'class': 'form-select w-full'}


class ReceitaForm(forms.Form):
    """Cabeçalho da receita — o que vale para a batida inteira."""

    produto = forms.ModelChoiceField(
        label='Produto acabado', queryset=Produto.objects.none(),
        widget=forms.Select(attrs=SELECT),
    )
    descricao = forms.CharField(
        label='Descrição da receita', max_length=150,
        widget=forms.TextInput(attrs={**ENTRADA, 'placeholder': 'Polpa de manga 100 g'}),
    )
    versao = forms.CharField(
        label='Versão', max_length=10, initial='1.0',
        widget=forms.TextInput(attrs=ENTRADA),
    )
    quantidade_produzida = forms.DecimalField(
        label='Rende por batida', min_value=0, initial=1,
        widget=forms.NumberInput(attrs={**ENTRADA, 'step': '0.001'}),
        help_text='Quantas unidades do produto saem de uma execução da receita.',
    )
    rendimento_esperado = forms.DecimalField(
        label='Rendimento esperado (%)', required=False, min_value=0,
        widget=forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
        help_text='Quanto do que entra vira produto. Ex.: manga ≈ 60%.',
    )
    tempo_producao_minutos = forms.IntegerField(
        label='Tempo estimado (min)', required=False, min_value=0,
        widget=forms.NumberInput(attrs=ENTRADA),
    )
    temperatura_processo_min = forms.DecimalField(
        label='Temperatura mínima (°C)', required=False,
        widget=forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
    )
    temperatura_processo_max = forms.DecimalField(
        label='Temperatura máxima (°C)', required=False,
        widget=forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
    )
    custo_mao_obra_padrao = forms.DecimalField(
        label='Mão de obra por batida (R$)', required=False, min_value=0, initial=0,
        widget=forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
    )
    custo_indireto_padrao = forms.DecimalField(
        label='Custo indireto por batida (R$)', required=False, min_value=0, initial=0,
        widget=forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
        help_text='Energia, depreciação, água — o que a batida consome sem ser insumo.',
    )
    observacoes_tecnicas = forms.CharField(
        label='Observações técnicas', required=False,
        widget=forms.Textarea(attrs={**ENTRADA, 'rows': 3}),
    )

    def __init__(self, *args, filial=None, receita=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        self.receita = receita

        # SÓ PRODUTO ACABADO tem receita. Oferecer o catálogo inteiro faria
        # alguém criar a receita "do pote de 500 ml", que não se fabrica.
        acabados = FichaProduto.objects.for_filial(filial).filter(
            classe=FichaProduto.Classe.ACABADO,
        ).values_list('produto_id', flat=True) if filial else []
        self.fields['produto'].queryset = (
            Produto.objects.for_filial(filial).filter(pk__in=list(acabados))
            if filial else Produto.objects.none()
        )

        if receita is not None:
            ficha = receita.ficha
            self.fields['produto'].disabled = True
            if not self.is_bound:
                self.initial.update({
                    'produto': ficha.produto_acabado_id,
                    'descricao': ficha.descricao, 'versao': ficha.versao,
                    'quantidade_produzida': ficha.quantidade_produzida,
                    'tempo_producao_minutos': ficha.tempo_producao_minutos,
                    'custo_mao_obra_padrao': ficha.custo_mao_obra_padrao,
                    'custo_indireto_padrao': ficha.custo_indireto_padrao,
                    'rendimento_esperado': receita.rendimento_esperado,
                    'temperatura_processo_min': receita.temperatura_processo_min,
                    'temperatura_processo_max': receita.temperatura_processo_max,
                    'observacoes_tecnicas': receita.observacoes_tecnicas,
                })

    def clean_rendimento_esperado(self):
        valor = self.cleaned_data.get('rendimento_esperado')
        # RENDIMENTO ACIMA DE 100% existe (açúcar e água somam massa), mas
        # acima de 300% é digitação — 60 virou 600 numa tecla presa.
        if valor is not None and valor > 300:
            raise forms.ValidationError(
                'Rendimento acima de 300% — confira o número.'
            )
        return valor


class ItemReceitaForm(forms.ModelForm):
    """Um ingrediente ou uma embalagem da receita."""

    class Meta:
        model = ItemFichaTecnica
        fields = ('materia_prima', 'quantidade', 'perda_prevista', 'observacao')
        widgets = {
            'materia_prima': forms.Select(attrs=SELECT),
            'quantidade': forms.NumberInput(attrs={**ENTRADA, 'step': '0.0001'}),
            'perda_prevista': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'observacao': forms.TextInput(attrs=ENTRADA),
        }

    def __init__(self, *args, filial=None, ficha=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.ficha = ficha
        # MATÉRIA-PRIMA E EMBALAGEM, e não o acabado: uma receita que consome
        # o próprio produto acabado é um laço que ninguém quer descobrir na
        # explosão de necessidade.
        insumos = FichaProduto.objects.for_filial(filial).exclude(
            classe=FichaProduto.Classe.ACABADO,
        ).values_list('produto_id', flat=True) if filial else []
        self.fields['materia_prima'].queryset = (
            Produto.objects.for_filial(filial).filter(pk__in=list(insumos))
            if filial else Produto.objects.none()
        )
        self.fields['materia_prima'].label = 'Insumo'
        self.fields['observacao'].required = False

    def clean(self):
        dados = super().clean()
        insumo = dados.get('materia_prima')
        if insumo and self.ficha:
            ja_tem = self.ficha.itens.filter(materia_prima=insumo)
            if self.instance.pk:
                ja_tem = ja_tem.exclude(pk=self.instance.pk)
            if ja_tem.exists():
                # Duas linhas do mesmo insumo dariam duas participações para
                # o mesmo ingrediente, e o percentual do rótulo sairia errado.
                self.add_error(
                    'materia_prima',
                    'Este insumo já está na receita — edite a linha existente.',
                )
        return dados


class EtapaReceitaForm(forms.ModelForm):
    """Uma etapa do processo."""

    class Meta:
        model = EtapaReceita
        fields = (
            'ordem', 'nome', 'etapa', 'equipamento', 'tempo_minutos',
            'temperatura_min', 'temperatura_max', 'perda_percentual', 'instrucao',
        )
        widgets = {
            'ordem': forms.NumberInput(attrs={**ENTRADA, 'min': 1}),
            'nome': forms.TextInput(attrs={**ENTRADA, 'placeholder': 'Despolpa'}),
            'etapa': forms.Select(attrs=SELECT),
            'equipamento': forms.TextInput(attrs={**ENTRADA, 'placeholder': 'Despolpadeira'}),
            'tempo_minutos': forms.NumberInput(attrs=ENTRADA),
            'temperatura_min': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'temperatura_max': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'perda_percentual': forms.NumberInput(attrs={**ENTRADA, 'step': '0.01'}),
            'instrucao': forms.Textarea(attrs={**ENTRADA, 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in ('equipamento', 'tempo_minutos', 'temperatura_min',
                      'temperatura_max', 'instrucao'):
            self.fields[campo].required = False

        # É AQUI QUE AS ETAPAS VIRAM CONFIGURÁVEIS. Escolher a etapa
        # canônica faz esta linha da receita virar apontamento na ordem —
        # e é assim que cada fábrica monta o seu caminho, em vez de aceitar
        # o padrão do tipo de produto. Em branco, a linha continua valendo
        # como instrução escrita.
        from apps.polpa.models.processo import Etapa

        self.fields['etapa'] = forms.ChoiceField(
            label='Etapa do processo',
            choices=[('', 'Só instrução — não vira apontamento')] + [
                (e.value, e.label) for e in Etapa
            ],
            required=False, widget=forms.Select(attrs=SELECT),
            help_text='Escolha para esta etapa ser apontada na ordem.',
        )

    def clean(self):
        dados = super().clean()
        minima = dados.get('temperatura_min')
        maxima = dados.get('temperatura_max')
        if minima is not None and maxima is not None and minima > maxima:
            self.add_error(
                'temperatura_max',
                'A mínima ficou acima da máxima — a faixa não existe.',
            )
        return dados
