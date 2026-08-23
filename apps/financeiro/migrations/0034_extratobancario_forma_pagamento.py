from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("financeiro", "0033_descricao_despesa_e_categorias_especiais"),
    ]

    operations = [
        migrations.AddField(
            model_name="extratobancario",
            name="forma_pagamento",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="financeiro.formapagamento",
            ),
        ),
    ]
