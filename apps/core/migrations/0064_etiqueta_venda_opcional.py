from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0063_configuracao_etiqueta_venda'),
    ]

    operations = [
        migrations.AlterField(
            model_name='configuracaoetiquetavenda',
            name='ativa',
            field=models.BooleanField(default=False),
        ),
    ]
