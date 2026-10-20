from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pdv', '0035_rotadelivery_conclusoes_pedidos'),
    ]

    operations = [
        migrations.AddField(
            model_name='itemvendapdv',
            name='apresentacao_codigo_barras',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Codigo de barras da apresentacao no momento da venda.',
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name='itemvendapdv',
            name='apresentacao_descricao_snapshot',
            field=models.CharField(blank=True, default='', max_length=60),
        ),
        migrations.AddField(
            model_name='itemvendapdv',
            name='apresentacao_unidade_snapshot',
            field=models.CharField(blank=True, default='', max_length=10),
        ),
    ]
