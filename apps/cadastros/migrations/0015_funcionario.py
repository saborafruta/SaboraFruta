from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("cadastros", "0014_vincular_rota_motorista_veiculo")]

    operations = [
        migrations.CreateModel(
            name="Funcionario",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("nome", models.CharField(max_length=150)),
                ("cpf", models.CharField(blank=True, db_index=True, max_length=11)),
                ("cargo", models.CharField(blank=True, max_length=100)),
                ("data_admissao", models.DateField(blank=True, null=True)),
                ("salario_base", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("email", models.EmailField(blank=True, max_length=120)),
                ("telefone", models.CharField(blank=True, max_length=20)),
                ("chave_pix", models.CharField(blank=True, max_length=150)),
                ("banco", models.CharField(blank=True, max_length=100)),
                ("agencia", models.CharField(blank=True, max_length=20)),
                ("conta", models.CharField(blank=True, max_length=30)),
                ("tipo_conta", models.CharField(blank=True, choices=[("corrente", "Conta corrente"), ("poupanca", "Conta poupanca"), ("pagamento", "Conta pagamento")], max_length=12)),
                ("observacao", models.TextField(blank=True)),
                ("ativo", models.BooleanField(db_index=True, default=True)),
                ("filial", models.ForeignKey(help_text="Filial proprietária do registro", on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.filial")),
            ],
            options={"db_table": "funcionarios", "ordering": ["nome"], "verbose_name": "Funcionario", "verbose_name_plural": "Funcionarios"},
        ),
        migrations.AddConstraint(
            model_name="funcionario",
            constraint=models.UniqueConstraint(condition=~models.Q(cpf=""), fields=("filial", "cpf"), name="uniq_funcionario_filial_cpf"),
        ),
        migrations.AddIndex(model_name="funcionario", index=models.Index(fields=["filial", "ativo"], name="func_filial_ativo_idx")),
        migrations.AddIndex(model_name="funcionario", index=models.Index(fields=["filial", "nome"], name="func_filial_nome_idx")),
    ]
