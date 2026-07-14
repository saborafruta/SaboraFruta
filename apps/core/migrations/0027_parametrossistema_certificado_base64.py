from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0026_parametrossistema_logo_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='parametrossistema',
            name='certificado_base64',
            field=models.TextField(
                blank=True,
                default='',
                help_text='Conteúdo do certificado A1 codificado em base64. Persiste entre redeploys.',
            ),
        ),
    ]
