import re

from django import forms

from .models import CatalogoConfiguracao, CupomCatalogo


DIAS_SEMANA = (
    ('0', 'Segunda'), ('1', 'Terça'), ('2', 'Quarta'), ('3', 'Quinta'),
    ('4', 'Sexta'), ('5', 'Sábado'), ('6', 'Domingo'),
)


class CatalogoConfiguracaoForm(forms.ModelForm):
    dias_funcionamento = forms.MultipleChoiceField(
        label='Dias de funcionamento', choices=DIAS_SEMANA,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = CatalogoConfiguracao
        exclude = ['filial', 'created_at', 'updated_at']
        labels = {
            'ativo': 'Catálogo ativo',
            'titulo': 'Título do catálogo',
            'descricao': 'Mensagem para o cliente',
            'pedido_minimo': 'Valor mínimo do pedido',
            'retirada_ativa': 'Permitir retirada na loja',
            'entrega_ativa': 'Permitir entrega',
            'agendamento_entrega_ativo': 'Permitir agendar data e horário',
            'frete_gratis_ativo': 'Oferecer frete grátis acima de um valor',
            'valor_minimo_frete_gratis': 'Frete grátis a partir de',
            'frete_abaixo_limite': 'Abaixo desse valor, o frete será',
            'valor_frete': 'Valor fixo do frete',
            'prazo_minimo_entrega_horas': 'Antecedência mínima para entrega (horas)',
            'horario_abertura': 'Abre às',
            'horario_fechamento': 'Fecha às',
        }
        widgets = {
            'descricao': forms.Textarea(attrs={'rows': 3}),
            'pedido_minimo': forms.NumberInput(attrs={'min': 0, 'step': '0.01'}),
            'valor_minimo_frete_gratis': forms.NumberInput(attrs={'min': 0, 'step': '0.01'}),
            'valor_frete': forms.NumberInput(attrs={'min': 0, 'step': '0.01'}),
            'prazo_minimo_entrega_horas': forms.NumberInput(attrs={'min': 0, 'max': 720}),
            'horario_abertura': forms.TimeInput(attrs={'type': 'time'}),
            'horario_fechamento': forms.TimeInput(attrs={'type': 'time'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            self.initial['dias_funcionamento'] = [
                str(dia) for dia in (self.instance.dias_funcionamento or [])
            ]

    def clean_dias_funcionamento(self):
        return [int(dia) for dia in self.cleaned_data['dias_funcionamento']]

    def clean(self):
        dados = super().clean()
        if not dados.get('retirada_ativa') and not dados.get('entrega_ativa'):
            raise forms.ValidationError('Ative entrega ou retirada para receber pedidos.')
        if dados.get('frete_gratis_ativo') and not dados.get('valor_minimo_frete_gratis'):
            self.add_error('valor_minimo_frete_gratis', 'Informe o valor mínimo para o frete grátis.')
        if (
            dados.get('frete_abaixo_limite') == CatalogoConfiguracao.FreteAbaixoLimite.FIXO
            and not dados.get('valor_frete')
        ):
            self.add_error('valor_frete', 'Informe o valor fixo do frete.')
        return dados


class CupomCatalogoForm(forms.ModelForm):
    class Meta:
        model = CupomCatalogo
        fields = ['codigo', 'tipo', 'valor', 'pedido_minimo', 'valido_de', 'valido_ate', 'ativo']
        labels = {
            'codigo': 'Código', 'tipo': 'Tipo de desconto', 'valor': 'Desconto',
            'pedido_minimo': 'Pedido mínimo', 'valido_de': 'Válido a partir de',
            'valido_ate': 'Válido até', 'ativo': 'Cupom ativo',
        }
        widgets = {
            'codigo': forms.TextInput(attrs={'placeholder': 'Ex.: PRIMEIRACOMPRA'}),
            'valor': forms.NumberInput(attrs={'min': '0.01', 'step': '0.01'}),
            'pedido_minimo': forms.NumberInput(attrs={'min': '0', 'step': '0.01'}),
            'valido_de': forms.DateInput(attrs={'type': 'date'}),
            'valido_ate': forms.DateInput(attrs={'type': 'date'}),
        }

    def clean_codigo(self):
        codigo = (self.cleaned_data['codigo'] or '').strip().upper()
        if not re.fullmatch(r'[A-Z0-9_-]+', codigo):
            raise forms.ValidationError('Use apenas letras, números, hífen ou sublinhado.')
        return codigo

    def clean(self):
        dados = super().clean()
        if dados.get('tipo') == CupomCatalogo.Tipo.PERCENTUAL and (dados.get('valor') or 0) > 100:
            self.add_error('valor', 'O desconto percentual não pode ser maior que 100%.')
        if dados.get('valido_de') and dados.get('valido_ate') and dados['valido_de'] > dados['valido_ate']:
            self.add_error('valido_ate', 'A data final deve ser igual ou posterior à inicial.')
        return dados
