from django import forms

from .models import ConfiguracaoWhatsApp


class ConfiguracaoWhatsAppForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoWhatsApp
        fields = ['agente_ativo', 'ativo']
        labels = {
            'agente_ativo': 'Responder clientes automaticamente',
            'ativo': 'Integração ativa',
        }


class FluxoWhatsAppForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoWhatsApp
        fields = [
            'mensagem_saudacao', 'mensagem_transferencia',
            'mensagem_encerramento', 'mensagem_opcao_invalida',
            'encerramento_automatico_ativo', 'tempo_inatividade_minutos',
        ]
        labels = {
            'mensagem_saudacao': 'Mensagem de saudação',
            'mensagem_transferencia': 'Mensagem de transferência',
            'mensagem_encerramento': 'Mensagem de encerramento',
            'mensagem_opcao_invalida': 'Mensagem para opção não reconhecida',
            'encerramento_automatico_ativo': 'Encerrar conversas automaticamente',
            'tempo_inatividade_minutos': 'Tempo de inatividade (minutos)',
        }
        widgets = {
            'mensagem_saudacao': forms.Textarea(attrs={'rows': 3}),
            'mensagem_transferencia': forms.Textarea(attrs={'rows': 3}),
            'mensagem_encerramento': forms.Textarea(attrs={'rows': 3}),
            'mensagem_opcao_invalida': forms.Textarea(attrs={'rows': 2}),
            'tempo_inatividade_minutos': forms.NumberInput(attrs={'min': 1, 'max': 10080}),
        }
