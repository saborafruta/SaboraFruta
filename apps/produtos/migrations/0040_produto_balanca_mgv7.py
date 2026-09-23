from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('produtos', '0039_apresentacao_filial_e_preco'),
    ]

    operations = [
        migrations.AlterField(
            model_name='produto',
            name='codigo_balanca',
            field=models.CharField(
                blank=True,
                help_text='PLU numerico usado na balanca (de 1 a 6 digitos)',
                max_length=6,
            ),
        ),
        migrations.AlterField(
            model_name='produto',
            name='gera_etiqueta_balanca',
            field=models.BooleanField(
                default=False,
                help_text='Inclui o produto no arquivo de carga para balancas Prix/MGV7.',
                verbose_name='Produto de balanca',
            ),
        ),
    ]
