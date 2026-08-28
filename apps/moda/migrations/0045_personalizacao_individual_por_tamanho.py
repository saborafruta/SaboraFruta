from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('moda', '0044_pedido_previsao_pagamento'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='personalizacaoindividual',
            options={
                'ordering': [
                    'tamanho__ordem', 'tamanho__sigla', 'ordem', 'id',
                ],
                'verbose_name': 'Personalização individual',
                'verbose_name_plural': 'Personalizações individuais',
            },
        ),
    ]
