# Generated manually: previsão comercial independente do financeiro.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('moda', '0043_alter_personalizacao_tecnica'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedidoproducao',
            name='previsao_pagamento',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    'Combinação comercial prevista no orçamento. Não gera nem se '
                    'vincula a lançamentos financeiros.'
                ),
            ),
        ),
    ]
