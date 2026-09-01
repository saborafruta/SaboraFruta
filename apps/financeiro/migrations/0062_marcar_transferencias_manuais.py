from django.db import migrations
from django.db.models import Q


def marcar_transferencias(apps, schema_editor):
    ExtratoBancario = apps.get_model("financeiro", "ExtratoBancario")
    ExtratoBancario.objects.filter(
        Q(historico__startswith="Transferencia para ")
        | Q(historico__startswith="Transferencia de "),
        origem="manual",
        tipo_lancamento="",
    ).update(tipo_lancamento="transferencia")


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0061_corrigir_classificacao_despesas_com_pessoal")]

    operations = [migrations.RunPython(marcar_transferencias, migrations.RunPython.noop)]
