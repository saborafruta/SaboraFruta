from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0025_formapagamento_conta_bancaria_padrao"),
        ("pdv", "0010_vendapdv_pagamentos_rascunho"),
    ]

    operations = [
        migrations.AddField(
            model_name="pagamentovendapdv",
            name="conta_bancaria",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="financeiro.contabancaria",
            ),
        ),
    ]
