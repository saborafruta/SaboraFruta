from django import forms

from .models import ConfiguracaoWhatsApp, ConfiguracaoWhatsAppCentral


STATUS_PEDIDO_NOTIFICACAO_CHOICES = (
    ('aprovado', 'Pedido aprovado'),
    ('em_separacao', 'Separação iniciada'),
    ('pendente_caixa', 'Pendente no caixa'),
    ('pago', 'Pagamento confirmado'),
    ('pronto', 'Separação concluída / pronto'),
    ('saiu_entrega', 'Saiu para entrega'),
    ('entregue', 'Pedido entregue'),
    ('cancelado', 'Pedido cancelado'),
)


class ConfiguracaoWhatsAppForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoWhatsApp
        fields = ['agente_ativo', 'ativo']
        labels = {
            'agente_ativo': 'Responder clientes automaticamente',
            'ativo': 'Integração ativa',
        }


class FluxoWhatsAppForm(forms.ModelForm):
    pedido_status_notificados = forms.MultipleChoiceField(
        label='Enviar atualização nestas etapas',
        choices=STATUS_PEDIDO_NOTIFICACAO_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text='Desmarque as etapas internas que não devem gerar mensagem no WhatsApp.',
    )

    class Meta:
        model = ConfiguracaoWhatsApp
        fields = [
            'modo_atendimento',
            'mensagem_saudacao', 'mensagem_transferencia',
            'mensagem_encerramento', 'mensagem_opcao_invalida',
            'mensagem_confirmacao_agendamento', 'mensagem_lembrete_agendamento',
            'mensagem_resumo_pedido', 'mensagem_pedido_recebido', 'mensagem_atualizacao_pedido',
            'pedido_status_notificados',
            'recuperacao_agendamento_ativa', 'recuperacao_atraso_minutos',
            'recuperacao_horario_inicio', 'recuperacao_horario_fim',
            'mensagem_recuperacao_agendamento',
            'mensagem_motivo_nao_agendamento', 'mensagem_feedback_agendamento',
            'encerramento_automatico_ativo', 'tempo_inatividade_minutos',
        ]
        labels = {
            'modo_atendimento': 'Serviços oferecidos pelo agente',
            'mensagem_saudacao': 'Mensagem de saudação',
            'mensagem_transferencia': 'Mensagem de transferência',
            'mensagem_encerramento': 'Mensagem de encerramento',
            'mensagem_opcao_invalida': 'Mensagem para opção não reconhecida',
            'mensagem_confirmacao_agendamento': 'Confirmação do agendamento',
            'mensagem_lembrete_agendamento': 'Lembrete do agendamento',
            'mensagem_resumo_pedido': 'Resumo final do pedido',
            'mensagem_pedido_recebido': 'Pedido confirmado pelo cliente',
            'mensagem_atualizacao_pedido': 'Atualização enviada pela loja',
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
            'mensagem_resumo_pedido': forms.Textarea(attrs={'rows': 13}),
            'mensagem_pedido_recebido': forms.Textarea(attrs={'rows': 3}),
            'mensagem_atualizacao_pedido': forms.Textarea(attrs={'rows': 5}),
            'mensagem_recuperacao_agendamento': forms.Textarea(attrs={'rows': 9}),
            'mensagem_motivo_nao_agendamento': forms.Textarea(attrs={'rows': 8}),
            'mensagem_feedback_agendamento': forms.Textarea(attrs={'rows': 3}),
            'recuperacao_atraso_minutos': forms.NumberInput(attrs={'min': 5, 'max': 10080}),
            'recuperacao_horario_inicio': forms.TimeInput(attrs={'type': 'time'}),
            'recuperacao_horario_fim': forms.TimeInput(attrs={'type': 'time'}),
            'tempo_inatividade_minutos': forms.NumberInput(attrs={'min': 1, 'max': 10080}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            self.initial['pedido_status_notificados'] = list(
                self.instance.pedido_status_notificados or [],
            )

    def clean_pedido_status_notificados(self):
        return list(self.cleaned_data.get('pedido_status_notificados') or [])


class ConfiguracaoWhatsAppCentralForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoWhatsAppCentral
        fields = [
            'instancia', 'resumos_ativos', 'horario_inicio', 'horario_fim',
            'intervalo_entre_envios_minutos', 'limite_diario', 'max_tentativas',
            'mensagem_resumo',
        ]
        labels = {
            'instancia': 'Nome da instância central',
            'resumos_ativos': 'Ativar envios automáticos',
            'horario_inicio': 'Iniciar os envios às',
            'horario_fim': 'Encerrar os envios às',
            'intervalo_entre_envios_minutos': 'Intervalo mínimo entre mensagens (minutos)',
            'limite_diario': 'Limite máximo de mensagens por dia',
            'max_tentativas': 'Máximo de tentativas por mensagem',
            'mensagem_resumo': 'Modelo da mensagem diária',
        }
        widgets = {
            'horario_inicio': forms.TimeInput(attrs={'type': 'time'}),
            'horario_fim': forms.TimeInput(attrs={'type': 'time'}),
            'intervalo_entre_envios_minutos': forms.NumberInput(attrs={'min': 1, 'max': 120}),
            'limite_diario': forms.NumberInput(attrs={'min': 1, 'max': 5000}),
            'max_tentativas': forms.NumberInput(attrs={'min': 1, 'max': 5}),
            'mensagem_resumo': forms.Textarea(attrs={'rows': 16}),
        }

    def clean(self):
        dados = super().clean()
        inicio = dados.get('horario_inicio')
        fim = dados.get('horario_fim')
        if inicio and fim and inicio >= fim:
            raise forms.ValidationError('O fim da janela deve ser posterior ao início.')
        return dados
