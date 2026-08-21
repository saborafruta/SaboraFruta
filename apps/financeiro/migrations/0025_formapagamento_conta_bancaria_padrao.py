from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0024_pagamentocontapagar_referencia_pagamento"),
    ]

    operations = [
        migrations.AddField(
            model_name="formapagamento",
            name="conta_bancaria_padrao",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="formas_pagamento_padrao",
                to="financeiro.contabancaria",
            ),
        ),
    ]
