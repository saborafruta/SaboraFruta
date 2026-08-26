"""
Formulário da etapa que a indústria cria.

O CÓDIGO É GERADO DO NOME quando a pessoa não o informa. Ele é chave técnica —
atravessa o apontamento, a receita e o indicador — e pedi-lo em branco na
primeira tela faria alguém digitar "Fermentação " com espaço no fim e passar a
semana sem entender por que a etapa some do relatório.

O CÓDIGO NÃO MUDA DEPOIS. Ele já está gravado em ordens apontadas e em linhas
de receita; trocá-lo desligaria a etapa do histórico dela sem avisar ninguém —
os apontamentos antigos ficariam órfãos, com o código velho e sem cadastro
que lhes dê nome.
"""
from django import forms
from django.utils.text import slugify

from apps.polpa.models import EtapaProcesso
from apps.polpa.models.processo import POSICAO, Etapa

ENTRADA = {'class': 'form-input w-full'}
SELECT = {'class': 'form-input w-full'}


class EtapaProcessoForm(forms.ModelForm):

    class Meta:
        model = EtapaProcesso
        fields = (
            'nome', 'codigo', 'sequencia', 'exige_peso',
            'exige_temperatura', 'instrucao', 'ativo',
        )
        widgets = {
            'nome': forms.TextInput(attrs={
                **ENTRADA, 'placeholder': 'Fermentação',
            }),
            'codigo': forms.TextInput(attrs={
                **ENTRADA, 'placeholder': 'deixe em branco para gerar do nome',
            }),
            'sequencia': forms.NumberInput(attrs={**ENTRADA, 'min': 0}),
            'instrucao': forms.Textarea(attrs={**ENTRADA, 'rows': 3}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filial = filial
        self.fields['codigo'].required = False
        self.fields['sequencia'].help_text = (
            'Onde ela entra na fila. As etapas do vocabulário comum vão de '
            '0 a {} — veja a régua ao lado.'.format(len(POSICAO) - 1)
        )
        if self.instance.pk:
            # Já existe apontamento gravado com este código.
            self.fields['codigo'].disabled = True
            self.fields['codigo'].help_text = (
                'O código não muda depois de criado: ele já está nas ordens '
                'apontadas e nas receitas que o usam.'
            )

    def clean_codigo(self):
        """
        Gera do nome quando vazio, e recusa o que colide com o vocabulário.

        A colisão é recusada aqui, e não só no modelo, para a pessoa ver o
        motivo no campo em vez de numa tela de erro.
        """
        codigo = (self.cleaned_data.get('codigo') or '').strip()
        if not codigo:
            codigo = slugify(self.data.get('nome', ''))[:20].replace('-', '_')
        if not codigo:
            raise forms.ValidationError('Informe o nome da etapa.')
        if codigo in Etapa.values:
            raise forms.ValidationError(
                f'"{codigo}" já é uma etapa do vocabulário comum — use-a na '
                f'receita em vez de recriá-la.'
            )
        return codigo

    def save(self, commit=True):
        etapa = super().save(commit=False)
        if self.filial and not etapa.filial_id:
            etapa.filial = self.filial
        if commit:
            etapa.save()
        return etapa
