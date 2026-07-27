from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("financeiro", "0012_mdfe_tipo_documento_fiscal"),
        ("logistica", "0007_rename_indexes_remove_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="mdfe",
            name="codigo_municipio_carregamento",
            field=models.CharField(blank=True, max_length=7),
        ),
        migrations.AddField(
            model_name="mdfe",
            name="codigo_municipio_descarregamento",
            field=models.CharField(blank=True, max_length=7),
        ),
        migrations.AddField(
            model_name="mdfe",
            name="data_cancelamento",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mdfe",
            name="documento_fiscal",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="mdfe_logistico",
                to="financeiro.documentofiscal",
            ),
        ),
        migrations.AddField(
            model_name="mdfe",
            name="justificativa_cancelamento",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="mdfe",
            name="mensagem_sefaz",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="mdfe",
            name="transporte_metadados",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="documentomdfe",
            name="documento_fiscal",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="vinculos_mdfe",
                to="financeiro.documentofiscal",
            ),
        ),
        migrations.AlterField(
            model_name="mdfe",
            name="status",
            field=models.CharField(
                choices=[
                    ("rascunho", "Rascunho"),
                    ("aguardando_nfe", "Aguardando autorização da NF-e"),
                    ("processando", "Processando autorização"),
                    ("autorizado", "Autorizado"),
                    ("rejeitado", "Rejeitado"),
                    ("encerrado", "Encerrado"),
                    ("cancelado", "Cancelado"),
                ],
                db_index=True,
                default="rascunho",
                max_length=20,
            ),
        ),
    ]
