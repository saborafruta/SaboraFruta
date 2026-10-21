import uuid

from django.db import migrations, models


def preencher_tokens(apps, schema_editor):
    Boleto = apps.get_model("financeiro", "Boleto")
    alias = schema_editor.connection.alias
    for boleto in Boleto.objects.using(alias).filter(public_token__isnull=True).iterator():
        boleto.public_token = uuid.uuid4()
        boleto.save(using=alias, update_fields=["public_token"])


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0069_cnab_cobranca")]

    operations = [
        migrations.AddField(
            model_name="boleto",
            name="public_token",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(preencher_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="boleto",
            name="public_token",
            field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False),
        ),
    ]
