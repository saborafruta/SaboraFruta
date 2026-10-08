from datetime import time

from django.db import migrations, models
import django.core.validators


def classificar_conversas_existentes(apps, schema_editor):
    Conversa = apps.get_model('whatsapp_agent', 'ConversaWhatsApp')
    Conversa.objects.filter(atendimento_humano=True).update(etapa_crm='atendimento_humano')
    Conversa.objects.filter(ativa=False).exclude(
        etapa_crm='atendimento_humano',
    ).update(etapa_crm='nao_convertido')


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp_agent', '0004_mensagens_agendamento'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='mensagem_recuperacao_agendamento',
            field=models.TextField(
                default=(
                    'Oi, {nome}! Você conseguiu escolher seu horário? 😊\n\n'
                    'Se precisar, posso ajudar por aqui.\n\n'
                    '*1.* Continuar o agendamento\n'
                    '*2.* Tenho uma dúvida\n'
                    '*3.* Não quero agendar agora\n\n'
                    '{link}'
                ),
                help_text='Variáveis disponíveis: {nome} e {link}.',
            ),
        ),
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='mensagem_motivo_nao_agendamento',
            field=models.TextField(
                default=(
                    'Tudo bem! Se puder, conte o principal motivo. Isso nos ajuda a melhorar:\n\n'
                    '*1.* Não encontrei um horário\n'
                    '*2.* O valor não serviu para mim\n'
                    '*3.* Não encontrei o serviço\n'
                    '*4.* Tive dificuldade para agendar\n'
                    '*5.* Vou agendar depois\n\n'
                    'Você também pode escrever outro motivo ou digitar *pular*.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='mensagem_feedback_agendamento',
            field=models.TextField(
                default='Obrigado pela resposta! Quando precisar, é só enviar *oi*. 👋',
            ),
        ),
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='recuperacao_agendamento_ativa',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='recuperacao_atraso_minutos',
            field=models.PositiveIntegerField(
                default=60,
                help_text='Tempo após o início do agendamento antes de enviar o acompanhamento.',
                validators=[
                    django.core.validators.MinValueValidator(5),
                    django.core.validators.MaxValueValidator(10080),
                ],
            ),
        ),
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='recuperacao_horario_fim',
            field=models.TimeField(default=time(19, 0)),
        ),
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='recuperacao_horario_inicio',
            field=models.TimeField(default=time(8, 0)),
        ),
        migrations.AddField(
            model_name='conversawhatsapp',
            name='acompanhamento_enviado_em',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='conversawhatsapp',
            name='agendamento_confirmado_em',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='conversawhatsapp',
            name='agendamento_iniciado_em',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='conversawhatsapp',
            name='etapa_crm',
            field=models.CharField(
                choices=[
                    ('nova', 'Novas conversas'),
                    ('interessado', 'Interessados'),
                    ('agendamento_iniciado', 'Agendamento iniciado'),
                    ('aguardando_cliente', 'Aguardando cliente'),
                    ('agendado', 'Agendados'),
                    ('atendimento_humano', 'Atendimento humano'),
                    ('concluido', 'Concluídos'),
                    ('nao_convertido', 'Não convertidos'),
                ],
                db_index=True,
                default='nova',
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name='conversawhatsapp',
            name='motivo_nao_agendamento',
            field=models.CharField(blank=True, max_length=180),
        ),
        migrations.RunPython(classificar_conversas_existentes, migrations.RunPython.noop),
    ]
