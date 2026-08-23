from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0034_extratobancario_forma_pagamento"),
    ]

    operations = [
        migrations.AddField(
            model_name="extratobancario",
            name="plano_contas",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="extratos_classificados",
                to="financeiro.planocontas",
            ),
        ),
    ]
