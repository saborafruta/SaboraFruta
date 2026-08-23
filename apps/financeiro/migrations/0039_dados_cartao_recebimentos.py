from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0038_completa_classificacao_receitas")]

    operations = [
        migrations.AddField(
            model_name="contareceber",
            name="bandeira_recebimento",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="contareceber",
            name="parcelas_recebimento",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="extratobancario",
            name="bandeira",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="extratobancario",
            name="numero_parcelas",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="extratobancario",
            name="prazo_compensacao_aplicado",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="extratobancario",
            name="taxa_calculada_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="extratobancario",
            name="taxa_fixa_aplicada",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name="extratobancario",
            name="taxa_percentual_aplicada",
            field=models.DecimalField(decimal_places=4, default=0, max_digits=7),
        ),
        migrations.AddField(
            model_name="extratobancario",
            name="valor_liquido",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name="extratobancario",
            name="valor_taxa",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
    ]
