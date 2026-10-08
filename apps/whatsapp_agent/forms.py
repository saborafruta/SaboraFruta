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
            'mensagem_confirmacao_agendamento', 'mensagem_lembrete_agendamento',
            'recuperacao_agendamento_ativa', 'recuperacao_atraso_minutos',
            'recuperacao_horario_inicio', 'recuperacao_horario_fim',
            'mensagem_recuperacao_agendamento',
            'mensagem_motivo_nao_agendamento', 'mensagem_feedback_agendamento',
            'encerramento_automatico_ativo', 'tempo_inatividade_minutos',
        ]
        labels = {
            'mensagem_saudacao': 'Mensagem de saudação',
            'mensagem_transferencia': 'Mensagem de transferência',
            'mensagem_encerramento': 'Mensagem de encerramento',
            'mensagem_opcao_invalida': 'Mensagem para opção não reconhecida',
            'mensagem_confirmacao_agendamento': 'Confirmação do agendamento',
            'mensagem_lembrete_agendamento': 'Lembrete do agendamento',
            'recuperacao_agendamento_ativa': 'Acompanhar agendamentos não concluídos',
            'recuperacao_atraso_minutos': 'Enviar acompanhamento após (minutos)',
            'recuperacao_horario_inicio': 'Enviar a partir de',
            'recuperacao_horario_fim': 'Enviar até',
            'mensagem_recuperacao_agendamento': 'Mensagem de recuperação',
            'mensagem_motivo_nao_agendamento': 'Pergunta sobre o motivo',
            'mensagem_feedback_agendamento': 'Agradecimento após a resposta',
            'encerramento_automatico_ativo': 'Encerrar conversas automaticamente',
            'tempo_inatividade_minutos': 'Tempo de inatividade (minutos)',
        }
        widgets = {
            'mensagem_saudacao': forms.Textarea(attrs={'rows': 3}),
            'mensagem_transferencia': forms.Textarea(attrs={'rows': 3}),
            'mensagem_encerramento': forms.Textarea(attrs={'rows': 3}),
            'mensagem_opcao_invalida': forms.Textarea(attrs={'rows': 2}),
            'mensagem_confirmacao_agendamento': forms.Textarea(attrs={'rows': 9}),
            'mensagem_lembrete_agendamento': forms.Textarea(attrs={'rows': 9}),
            'mensagem_recuperacao_agendamento': forms.Textarea(attrs={'rows': 9}),
            'mensagem_motivo_nao_agendamento': forms.Textarea(attrs={'rows': 8}),
            'mensagem_feedback_agendamento': forms.Textarea(attrs={'rows': 3}),
            'recuperacao_atraso_minutos': forms.NumberInput(attrs={'min': 5, 'max': 10080}),
            'recuperacao_horario_inicio': forms.TimeInput(attrs={'type': 'time'}),
            'recuperacao_horario_fim': forms.TimeInput(attrs={'type': 'time'}),
            'tempo_inatividade_minutos': forms.NumberInput(attrs={'min': 1, 'max': 10080}),
        }
