from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("pdv", "0024_instalacoes_pdv_offline")]

    operations = [
        migrations.AddField(
            model_name="instalacaopdvoffline",
            name="fila_pendente_quantidade",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="instalacaopdvoffline",
            name="fila_erro_quantidade",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="instalacaopdvoffline",
            name="catalogo_atualizado_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="instalacaopdvoffline",
            name="ultima_sincronizacao_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="instalacaopdvoffline",
            name="ultimo_backup_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="instalacaopdvoffline",
            name="ultimo_erro_sincronizacao",
            field=models.TextField(blank=True),
        ),
        migrations.AddIndex(
            model_name="instalacaopdvoffline",
            index=models.Index(fields=["fila_pendente_quantidade", "-visto_por_ultimo_em"], name="pdv_off_fila_visto_idx"),
        ),
    ]
