from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("moda", "0061_peso_tecido_grade"),
    ]

    operations = [
        migrations.AddField(
            model_name="aviamento",
            name="tipo_personalizado",
            field=models.CharField(
                blank=True,
                help_text='Nome do tipo quando nenhum da lista serve (ex.: Patch). Vale só para "Outro aviamento".',
                max_length=40,
                verbose_name="Tipo personalizado",
            ),
        ),
    ]
