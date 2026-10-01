from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp_agent', '0002_configuracaowhatsapp_mensagem_encerramento_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='encerramento_automatico_ativo',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='configuracaowhatsapp',
            name='tempo_inatividade_minutos',
            field=models.PositiveIntegerField(
                default=60,
                help_text='Tempo sem mensagens antes de encerrar a conversa, entre 1 minuto e 7 dias.',
                validators=[MinValueValidator(1), MaxValueValidator(10080)],
            ),
        ),
    ]
