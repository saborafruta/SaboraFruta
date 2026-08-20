from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0019_contapagar_recorrencia")]

    operations = [
        migrations.AddField(
            model_name="contapagar",
            name="ajustar_vencimento_dia_util",
            field=models.BooleanField(default=False),
        ),
    ]
