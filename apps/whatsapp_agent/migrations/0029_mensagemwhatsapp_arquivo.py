from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('whatsapp_agent', '0028_unificar_conversas_vendedor_por_telefone'),
    ]

    operations = [
        migrations.AddField(
            model_name='mensagemwhatsapp',
            name='arquivo',
            field=models.FileField(
                blank=True, max_length=500,
                upload_to='whatsapp/mensagens/%Y/%m/%d/',
            ),
        ),
        migrations.AddField(
            model_name='mensagemwhatsapp',
            name='arquivo_mimetype',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='mensagemwhatsapp',
            name='arquivo_nome_original',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='mensagemwhatsapp',
            name='arquivo_tamanho',
            field=models.PositiveBigIntegerField(default=0),
        ),
    ]
