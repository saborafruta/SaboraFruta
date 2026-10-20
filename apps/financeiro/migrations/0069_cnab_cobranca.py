from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0068_documentofiscal_contingencia")]

    operations = [
        migrations.AddField(
            model_name="contabancaria", name="layout_cobranca",
            field=models.CharField(
                choices=[("cnab240", "CNAB 240 (FEBRABAN)"), ("cnab400", "CNAB 400")],
                default="cnab240", max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="contabancaria", name="codigo_transmissao",
            field=models.CharField(blank=True, help_text="Código do beneficiário/convênio usado no arquivo de cobrança.", max_length=20),
        ),
        migrations.AddField(
            model_name="contabancaria", name="remessa_homologada",
            field=models.BooleanField(default=False, help_text="Marque somente depois que o banco homologar um arquivo gerado."),
        ),
        migrations.AddField(model_name="remessabancaria", name="layout", field=models.CharField(default="cnab240", max_length=10)),
        migrations.AddField(model_name="remessabancaria", name="arquivo_hash", field=models.CharField(blank=True, db_index=True, max_length=64)),
        migrations.AddField(model_name="retornobancario", name="layout", field=models.CharField(blank=True, max_length=10)),
        migrations.AddField(model_name="retornobancario", name="arquivo_hash", field=models.CharField(db_index=True, default="", max_length=64), preserve_default=False),
        migrations.AddField(model_name="retornobancario", name="resumo_processamento", field=models.JSONField(blank=True, default=dict)),
        migrations.AddConstraint(
            model_name="retornobancario",
            constraint=models.UniqueConstraint(fields=("filial", "arquivo_hash"), name="fin_retorno_hash_filial_uniq"),
        ),
        migrations.CreateModel(
            name="RetornoBancarioItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("numero_linha", models.PositiveIntegerField()),
                ("nosso_numero", models.CharField(blank=True, max_length=30)),
                ("codigo_ocorrencia", models.CharField(blank=True, max_length=3)),
                ("descricao_ocorrencia", models.CharField(blank=True, max_length=120)),
                ("data_ocorrencia", models.DateField(blank=True, null=True)),
                ("valor_titulo", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("valor_pago", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("valor_tarifa", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("status", models.CharField(default="pendente", max_length=20)),
                ("mensagem", models.CharField(blank=True, max_length=300)),
                ("boleto", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ocorrencias_retorno", to="financeiro.boleto")),
                ("retorno", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="itens", to="financeiro.retornobancario")),
            ],
            options={"db_table": "retornos_bancarios_itens", "ordering": ["numero_linha"]},
        ),
        migrations.AddConstraint(
            model_name="retornobancarioitem",
            constraint=models.UniqueConstraint(fields=("retorno", "numero_linha"), name="fin_retorno_item_linha_uniq"),
        ),
    ]
