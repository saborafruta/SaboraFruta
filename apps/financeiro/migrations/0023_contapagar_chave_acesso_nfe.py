from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0022_pagamentocontapagar_comprovante_arquivo_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='contapagar',
            name='chave_acesso_nfe',
            field=models.CharField(blank=True, db_index=True, max_length=44),
        ),
    ]
