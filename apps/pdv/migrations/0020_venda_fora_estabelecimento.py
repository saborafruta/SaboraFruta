from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pdv", "0019_venda_bonificacao"),
    ]

    operations = [
        migrations.AddField(
            model_name="vendapdv",
            name="venda_fora_estabelecimento",
            field=models.BooleanField(default=False),
        ),
    ]
