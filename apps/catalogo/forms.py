from django import forms

from .models import CatalogoConfiguracao


class CatalogoConfiguracaoForm(forms.ModelForm):
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
        }
        widgets = {
            'descricao': forms.Textarea(attrs={'rows': 3}),
            'pedido_minimo': forms.NumberInput(attrs={'min': 0, 'step': '0.01'}),
            'valor_minimo_frete_gratis': forms.NumberInput(attrs={'min': 0, 'step': '0.01'}),
            'valor_frete': forms.NumberInput(attrs={'min': 0, 'step': '0.01'}),
            'prazo_minimo_entrega_horas': forms.NumberInput(attrs={'min': 0, 'max': 720}),
        }

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
