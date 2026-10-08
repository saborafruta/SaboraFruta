from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0076_parametrossistema_resumo_whatsapp_ativo_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='parametrossistema',
            name='resumo_whatsapp_ativo',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Inclui esta filial na rotina programada. O envio manual continua '
                    'disponível na Central de Instâncias mesmo com esta opção desativada.'
                ),
                verbose_name='Ativar envio automático do resumo diário',
            ),
        ),
    ]
