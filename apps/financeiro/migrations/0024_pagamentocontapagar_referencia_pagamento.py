from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0023_contapagar_chave_acesso_nfe'),
    ]

    operations = [
        migrations.AddField(
            model_name='pagamentocontapagar',
            name='referencia_pagamento',
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
