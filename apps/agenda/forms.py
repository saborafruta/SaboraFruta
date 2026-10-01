import re

from django import forms

from apps.cadastros.models import Cliente, Funcionario
from apps.produtos.models import Produto

from .models import Agendamento, BloqueioAgenda, ProfissionalAgenda


class ProfissionalAgendaForm(forms.ModelForm):
    servicos = forms.ModelMultipleChoiceField(
        queryset=Produto.objects.none(),
        required=True,
        widget=forms.CheckboxSelectMultiple,
        label='Serviços realizados',
    )

    class Meta:
        model = ProfissionalAgenda
        fields = ['funcionario', 'servicos', 'intervalo_padrao_minutos', 'cor', 'ativo']
        widgets = {'cor': forms.HiddenInput()}

    def __init__(self, *args, filial, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['funcionario'].queryset = Funcionario.objects.for_filial(filial).filter(ativo=True)
        self.fields['servicos'].queryset = Produto.objects.for_filial(filial).filter(
            ativo=True, tipo_produto=Produto.TipoProduto.SERVICO, agendavel=True,
        )
        if self.instance.pk:
            self.fields['servicos'].initial = self.instance.servicos_vinculados.filter(ativo=True).values_list('servico_id', flat=True)

    def clean_cor(self):
        cor = (self.cleaned_data.get('cor') or '').strip().lower()
        if not re.fullmatch(r'#[0-9a-f]{6}', cor):
            raise forms.ValidationError('Selecione uma cor válida para o profissional.')
        return cor


class AgendamentoForm(forms.Form):
    profissional = forms.ModelChoiceField(queryset=ProfissionalAgenda.objects.none())
    servicos = forms.ModelMultipleChoiceField(queryset=Produto.objects.none())
    cliente = forms.ModelChoiceField(queryset=Cliente.objects.none(), required=False)
    pessoa_atendida_nome = forms.CharField(max_length=150, label='Pessoa atendida')
    telefone_contato = forms.CharField(max_length=20, required=False, label='Telefone')
    inicio = forms.DateTimeField(label='Data e horário', widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'))
    observacao = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}), label='Observação')

    def __init__(self, *args, filial, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['profissional'].queryset = ProfissionalAgenda.objects.for_filial(filial).filter(ativo=True).select_related('funcionario')
        self.fields['servicos'].queryset = Produto.objects.for_filial(filial).filter(
            ativo=True, tipo_produto=Produto.TipoProduto.SERVICO, agendavel=True,
        )
        self.fields['cliente'].queryset = Cliente.objects.for_filial(filial).filter(ativo=True)
        self.fields['inicio'].input_formats = ['%Y-%m-%dT%H:%M']

    def clean(self):
        cleaned = super().clean()
        cliente = cleaned.get('cliente')
        if cliente and not cleaned.get('pessoa_atendida_nome'):
            cleaned['pessoa_atendida_nome'] = cliente.nome_display
        return cleaned


class BloqueioAgendaForm(forms.ModelForm):
    class Meta:
        model = BloqueioAgenda
        fields = ['profissional', 'inicio', 'fim', 'motivo']
        widgets = {
            'inicio': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'fim': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
        }

    def __init__(self, *args, filial, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['profissional'].queryset = (
            ProfissionalAgenda.objects.for_filial(filial)
            .filter(ativo=True)
            .select_related('funcionario')
        )
        self.fields['profissional'].required = False
        self.fields['profissional'].empty_label = 'Toda a empresa'
        self.fields['inicio'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['fim'].input_formats = ['%Y-%m-%dT%H:%M']
