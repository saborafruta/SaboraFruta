import django.core.validators
import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("moda", "0060_pedidoproducao_motivo_ajuste_financeiro"),
    ]

    operations = [
        migrations.CreateModel(
            name="PesoTecidoGrade",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "tipo_peca",
                    models.CharField(
                        help_text='O mesmo "Tipo de peça" escolhido na OP 2.0 (ex.: Camisa, Camisa Polo).',
                        max_length=80,
                    ),
                ),
                (
                    "peso_g",
                    models.DecimalField(
                        blank=True,
                        decimal_places=1,
                        max_digits=8,
                        null=True,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0"))
                        ],
                        verbose_name="Peso (g)",
                    ),
                ),
                ("ordem", models.PositiveIntegerField(default=0)),
                (
                    "filial",
                    models.ForeignKey(
                        help_text="Filial proprietária do registro",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.filial",
                    ),
                ),
                (
                    "tecido",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="pesos_grade",
                        to="moda.tecido",
                    ),
                ),
                (
                    "grade",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="moda.grade",
                    ),
                ),
                (
                    "tamanho",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="moda.tamanho",
                    ),
                ),
            ],
            options={
                "verbose_name": "Peso por tecido e grade",
                "verbose_name_plural": "Pesos por tecido e grade",
                "db_table": "moda_pesos_tecido_grade",
                "ordering": ["tecido__nome", "tipo_peca", "grade__nome", "ordem"],
            },
        ),
        migrations.AddIndex(
            model_name="pesotecidograde",
            index=models.Index(
                fields=["filial", "tecido", "tipo_peca"],
                name="moda_pesos__filial__77620f_idx",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="pesotecidograde",
            unique_together={("filial", "tecido", "tipo_peca", "grade", "tamanho")},
        ),
    ]
