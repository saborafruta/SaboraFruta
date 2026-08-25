"""
O formulário da ordem de produção.

SÓ RECEITA ATIVA aparece: produzir por uma versão em rascunho é produzir
por uma fórmula que alguém ainda está mexendo. O serviço cobra a mesma
regra — a tela filtra, o servidor decide.
"""
from django import forms

from apps.core.models import Usuario
from apps.polpa.models import Receita
from apps.producao.models import FichaTecnica

ENTRADA = {'class': 'form-input w-full'}
SELECT = {'class': 'form-select w-full'}


class OrdemPolpaForm(forms.Form):
    receita = forms.ModelChoiceField(
        label='Receita', queryset=Receita.objects.none(),
        widget=forms.Select(attrs=SELECT),
        help_text='Só aparecem as versões ativas.',
    )
    quantidade_planejada = forms.DecimalField(
        label='Quantidade a produzir', min_value=0,
        widget=forms.NumberInput(attrs={**ENTRADA, 'step': '0.001'}),
    )
    responsavel = forms.ModelChoiceField(
        label='Responsável', queryset=Usuario.objects.none(), required=False,
        widget=forms.Select(attrs=SELECT),
    )
    data_inicio_prevista = forms.DateTimeField(
        label='Início previsto', required=False,
        widget=forms.DateTimeInput(attrs={**ENTRADA, 'type': 'datetime-local'},
                                   format='%Y-%m-%dT%H:%M'),
    )
    data_fim_prevista = forms.DateTimeField(
        label='Término previsto', required=False,
        widget=forms.DateTimeInput(attrs={**ENTRADA, 'type': 'datetime-local'},
                                   format='%Y-%m-%dT%H:%M'),
    )
    observacao = forms.CharField(
        label='Observação', required=False,
        widget=forms.Textarea(attrs={**ENTRADA, 'rows': 2}),
    )

    def __init__(self, *args, filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['receita'].queryset = (
            Receita.objects.for_filial(filial)
            .filter(ficha__status=FichaTecnica.Status.ATIVA)
            .select_related('ficha', 'ficha__produto_acabado')
            if filial else Receita.objects.none()
        )
        self.fields['responsavel'].queryset = (
            Usuario.objects.filter(empresa=filial.empresa, ativo=True)
            if filial else Usuario.objects.none()
        )
        self.fields['responsavel'].empty_label = 'Quem abriu'

    def clean(self):
        dados = super().clean()
        inicio = dados.get('data_inicio_prevista')
        fim = dados.get('data_fim_prevista')
        # TÉRMINO ANTES DO INÍCIO é digitação, e passaria batido até a OP
        # nascer atrasada no mesmo instante em que foi criada.
        if inicio and fim and fim < inicio:
            self.add_error(
                'data_fim_prevista',
                'O término ficou antes do início — confira as datas.',
            )
        return dados
