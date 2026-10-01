from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cadastros", "0016_analise_custo_compra"),
    ]

    operations = [
        migrations.AddField(
            model_name="funcionario",
            name="foto",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to="funcionarios/fotos/",
            ),
        ),
    ]
