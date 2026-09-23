from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('moda', '0063_aviamento_valores'),
    ]

    operations = [
        migrations.AlterField(
            model_name='arquivopedido',
            name='descricao',
            field=models.CharField(
                blank=True,
                help_text=(
                    'O que é este arquivo. Ex.: escudo em curva, '
                    'planilha de nomes.'
                ),
                max_length=500,
            ),
        ),
    ]
