from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('core', '0074_parametros_contingencia_nfce')]

    operations = [
        migrations.AddField(
            model_name='parametrossistema',
            name='balanca_ean_conteudo',
            field=models.CharField(
                choices=[('preco_total', 'Preço total'), ('peso', 'Peso líquido')],
                default='preco_total',
                help_text='Informe se a etiqueta codifica o preço total ou o peso líquido.',
                max_length=20,
                verbose_name='Conteúdo variável do EAN',
            ),
        ),
        migrations.AddField(
            model_name='parametrossistema',
            name='balanca_ean_plu_digitos',
            field=models.PositiveSmallIntegerField(
                choices=[(3, '3 dígitos'), (4, '4 dígitos'), (5, '5 dígitos'), (6, '6 dígitos')],
                default=5,
                help_text='Use a mesma quantidade definida em Configuração do EAN-13 no MGV7.',
                verbose_name='Quantidade de dígitos do PLU',
            ),
        ),
        migrations.AddField(
            model_name='parametrossistema',
            name='balanca_ean_prefixo',
            field=models.CharField(
                default='20',
                help_text='Um ou dois dígitos configurados no MGV7 para as etiquetas de peso.',
                max_length=2,
                verbose_name='Prefixo do EAN da balança',
            ),
        ),
    ]
