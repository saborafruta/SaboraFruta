from django import forms

from .models import ConfiguracaoWhatsApp


class ConfiguracaoWhatsAppForm(forms.ModelForm):
    api_key = forms.CharField(
        required=False, label='Chave da Evolution API',
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}),
        help_text='A chave atual nunca é exibida. Deixe em branco para mantê-la.',
    )

    class Meta:
        model = ConfiguracaoWhatsApp
        fields = [
            'gateway_url', 'api_key', 'instancia', 'agente_ativo',
            'mensagem_saudacao', 'mensagem_transferencia', 'ativo',
        ]
        labels = {
            'gateway_url': 'URL da Evolution API',
            'instancia': 'Nome da instância',
            'agente_ativo': 'Responder clientes automaticamente',
            'ativo': 'Integração ativa',
        }
        widgets = {
            'mensagem_saudacao': forms.Textarea(attrs={'rows': 3}),
            'mensagem_transferencia': forms.Textarea(attrs={'rows': 3}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('ativo') and not (cleaned.get('api_key') or self.instance.api_key_configurada):
            self.add_error('api_key', 'Informe a chave da Evolution API.')
        return cleaned

    def save(self, commit=True):
        instancia = super().save(commit=False)
        instancia.definir_api_key(self.cleaned_data.get('api_key'))
        if commit:
            instancia.save()
        return instancia
