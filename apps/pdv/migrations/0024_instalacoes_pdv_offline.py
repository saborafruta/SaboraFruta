from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("pdv", "0023_item_venda_snapshot_apresentacao")]

    operations = [
        migrations.CreateModel(
            name="InstalacaoPDVOffline",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tenant_alias", models.CharField(blank=True, db_index=True, max_length=100)),
                ("empresa_id_origem", models.BigIntegerField(blank=True, db_index=True, null=True)),
                ("filial_id_origem", models.BigIntegerField(db_index=True)),
                ("filial_nome", models.CharField(max_length=160)),
                ("usuario_id_origem", models.BigIntegerField(db_index=True)),
                ("usuario_nome", models.CharField(max_length=160)),
                ("usuario_login", models.CharField(blank=True, max_length=254)),
                ("installation_id", models.CharField(db_index=True, max_length=80)),
                ("nome_dispositivo", models.CharField(max_length=120)),
                ("caixa_id_origem", models.BigIntegerField(blank=True, null=True)),
                ("caixa_descricao", models.CharField(blank=True, max_length=120)),
                ("user_agent", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("ativa", "Ativa"), ("inativa", "Desativada no PDV"), ("redefinir", "Aguardando novo PIN"), ("revogada", "Revogada")], db_index=True, default="ativa", max_length=20)),
                ("revisao", models.PositiveIntegerField(default=1)),
                ("recuperacao_configurada", models.BooleanField(default=False)),
                ("autorizado_em", models.DateTimeField(auto_now_add=True)),
                ("visto_por_ultimo_em", models.DateTimeField(auto_now=True, db_index=True)),
                ("revogado_em", models.DateTimeField(blank=True, null=True)),
                ("revogado_por_id", models.BigIntegerField(blank=True, null=True)),
                ("revogado_por_nome", models.CharField(blank=True, max_length=160)),
                ("motivo_revogacao", models.TextField(blank=True)),
            ],
            options={"db_table": "pdv_instalacoes_offline", "ordering": ["-visto_por_ultimo_em"]},
        ),
        migrations.CreateModel(
            name="EventoInstalacaoPDVOffline",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tipo", models.CharField(db_index=True, max_length=30)),
                ("ator_id", models.BigIntegerField(blank=True, null=True)),
                ("ator_nome", models.CharField(blank=True, max_length=160)),
                ("detalhe", models.TextField(blank=True)),
                ("metadados", models.JSONField(blank=True, default=dict)),
                ("criado_em", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("instalacao", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="eventos", to="pdv.instalacaopdvoffline")),
            ],
            options={"db_table": "pdv_instalacoes_offline_eventos", "ordering": ["-criado_em"]},
        ),
        migrations.AddConstraint(
            model_name="instalacaopdvoffline",
            constraint=models.UniqueConstraint(fields=("tenant_alias", "filial_id_origem", "usuario_id_origem", "installation_id"), name="uniq_pdv_offline_escopo_instalacao"),
        ),
        migrations.AddIndex(
            model_name="instalacaopdvoffline",
            index=models.Index(fields=["status", "-visto_por_ultimo_em"], name="pdv_off_status_visto_idx"),
        ),
    ]
