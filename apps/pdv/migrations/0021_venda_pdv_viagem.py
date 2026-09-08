import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("logistica", "0031_venda_pdv_na_expedicao"),
        ("pdv", "0020_venda_fora_estabelecimento"),
    ]

    operations = [
        migrations.AddField(
            model_name="vendapdv",
            name="viagem",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="vendas_pdv_fora",
                to="logistica.viagem",
            ),
        ),
    ]
