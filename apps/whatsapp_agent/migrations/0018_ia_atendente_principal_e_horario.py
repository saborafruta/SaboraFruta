import datetime

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp_agent', '0017_configuracaowhatsapp_ia_api_key_criptografada_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='configuracaowhatsapp',
            name='ia_ativa',
            field=models.BooleanField(
                default=False,
                help_text='Usa IA como atendente principal em qualquer mensagem recebida.',
            ),
        ),
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='ia_restringir_horario',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'Fora desta janela, envia o aviso automático e encaminha para '
                    'atendimento humano.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='ia_horario_inicio',
            field=models.TimeField(default=datetime.time(8, 0)),
        ),
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='ia_horario_fim',
            field=models.TimeField(default=datetime.time(20, 0)),
        ),
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='ia_mensagem_fora_horario',
            field=models.TextField(
                default=(
                    'Olá! Recebemos sua mensagem, mas nosso time não está disponível agora. '
                    'Nosso atendimento funciona das {inicio} às {fim}. '
                    'Assim que um atendente estiver disponível, entraremos em contato com você.'
                ),
                help_text='Variáveis disponíveis: {inicio} e {fim}.',
            ),
        ),
    ]
