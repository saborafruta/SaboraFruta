from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0018_funcionario_conta_pagar_categorias_pessoal")]

    operations = [
        migrations.AddField(
            model_name="contapagar", name="grupo_recorrencia",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="contapagar", name="frequencia_recorrencia",
            field=models.CharField(blank=True, choices=[("semanal", "Semanal"), ("mensal", "Mensal"), ("trimestral", "Trimestral"), ("semestral", "Semestral"), ("anual", "Anual")], max_length=12),
        ),
        migrations.AddIndex(
            model_name="contapagar",
            index=models.Index(fields=["filial", "grupo_recorrencia"], name="contas_paga_filial__cec514_idx"),
        ),
    ]
