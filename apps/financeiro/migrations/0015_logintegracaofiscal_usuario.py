from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("financeiro", "0014_alter_formapagamento_tipo"),
    ]

    operations = [
        migrations.AddField(
            model_name="logintegracaofiscal",
            name="usuario",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="logs_integracao_fiscal",
                to="core.usuario",
            ),
        ),
    ]
