from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('whatsapp_agent', '0018_ia_atendente_principal_e_horario'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='ia_mensagem_erro',
            field=models.TextField(
                default=(
                    'Estou com uma instabilidade no atendimento automático agora. '
                    'Recebi sua mensagem e vou encaminhá-la para nossa equipe continuar com você.'
                ),
                help_text=(
                    'Enviada somente quando a IA não consegue responder. A conversa é '
                    'encaminhada automaticamente para atendimento humano.'
                ),
            ),
        ),
    ]
