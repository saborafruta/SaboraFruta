from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("financeiro", "0070_boleto_public_token"),
    ]

    operations = [
        migrations.AlterField(
            model_name="contabancaria",
            name="convenio",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="contabancaria",
            name="digito_convenio",
            field=models.CharField(blank=True, max_length=2),
        ),
        migrations.AddField(
            model_name="contabancaria",
            name="variacao_carteira",
            field=models.CharField(blank=True, max_length=2),
        ),
        migrations.AddField(
            model_name="contabancaria",
            name="posto_cobranca",
            field=models.CharField(blank=True, max_length=2),
        ),
        migrations.AddField(
            model_name="contabancaria",
            name="byte_identificacao",
            field=models.CharField(blank=True, max_length=1),
        ),
        migrations.AddField(
            model_name="contabancaria",
            name="portfolio_cobranca",
            field=models.CharField(blank=True, max_length=3),
        ),
        migrations.AddField(
            model_name="contabancaria",
            name="incremento_cobranca",
            field=models.CharField(blank=True, max_length=3),
        ),
        migrations.AddField(
            model_name="contabancaria",
            name="numero_contrato_cobranca",
            field=models.CharField(blank=True, max_length=7),
        ),
    ]
