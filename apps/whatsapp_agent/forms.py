from django import forms

from .models import ConfiguracaoWhatsApp


class ConfiguracaoWhatsAppForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoWhatsApp
        fields = [
            'agente_ativo', 'mensagem_saudacao', 'mensagem_transferencia', 'ativo',
        ]
        labels = {
            'agente_ativo': 'Responder clientes automaticamente',
            'ativo': 'Integração ativa',
        }
        widgets = {
            'mensagem_saudacao': forms.Textarea(attrs={'rows': 3}),
            'mensagem_transferencia': forms.Textarea(attrs={'rows': 3}),
        }
