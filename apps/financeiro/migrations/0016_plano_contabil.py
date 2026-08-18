import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("financeiro", "0015_logintegracaofiscal_usuario"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlanoContabil",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("ativo", models.BooleanField(default=True, verbose_name="Ativo")),
                ("codigo_referencia", models.PositiveIntegerField()),
                ("classificacao", models.CharField(max_length=20)),
                ("tipo_conta", models.CharField(choices=[("S", "Sintética"), ("A", "Analítica")], max_length=1)),
                ("descricao", models.CharField(max_length=255)),
                ("codigo_dre", models.CharField(blank=True, max_length=20)),
                ("data_inicio", models.DateField()),
                ("nivel", models.PositiveSmallIntegerField()),
                ("ordem", models.PositiveIntegerField()),
                ("pagina_origem", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("origem", models.CharField(default="Relação de Contas - Contabilidade", max_length=120)),
                ("conta_pai", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="contas_filhas", to="financeiro.planocontabil")),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="plano_contabil", to="core.empresa")),
            ],
            options={
                "verbose_name": "Conta contábil",
                "verbose_name_plural": "Plano contábil",
                "db_table": "plano_contabil",
                "ordering": ["ordem"],
                "indexes": [
                    models.Index(fields=["empresa", "ordem"], name="pl_cont_emp_ord_idx"),
                    models.Index(fields=["empresa", "tipo_conta"], name="pl_cont_emp_tipo_idx"),
                    models.Index(fields=["empresa", "ativo"], name="pl_cont_emp_ativo_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("empresa", "classificacao"), name="uniq_plano_contabil_empresa_classificacao"),
                    models.UniqueConstraint(fields=("empresa", "codigo_referencia"), name="uniq_plano_contabil_empresa_codigo_ref"),
                ],
            },
        ),
    ]
