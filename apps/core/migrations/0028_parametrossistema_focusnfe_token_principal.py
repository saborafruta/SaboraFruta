from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0027_parametrossistema_certificado_base64'),
    ]

    operations = [
        migrations.AddField(
            model_name='parametrossistema',
            name='focusnfe_token_principal',
            field=models.CharField(
                blank=True,
                help_text=(
                    'Token Principal de Producao usado somente para gerenciar '
                    'a empresa na Focus.'
                ),
                max_length=255,
            ),
        ),
    ]
