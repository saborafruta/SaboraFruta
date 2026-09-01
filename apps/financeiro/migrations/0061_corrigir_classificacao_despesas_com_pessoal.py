from django.db import migrations


CODIGOS_DESPESAS_COM_PESSOAL = (
    "33201",
    "3320100017",
    "3320100018",
)


def corrigir_classificacao(apps, schema_editor):
    PlanoContas = apps.get_model("financeiro", "PlanoContas")
    PlanoContas.objects.filter(
        codigo__in=CODIGOS_DESPESAS_COM_PESSOAL,
    ).update(despesa_pessoal=False)


def restaurar_classificacao_anterior(apps, schema_editor):
    PlanoContas = apps.get_model("financeiro", "PlanoContas")
    PlanoContas.objects.filter(
        codigo__in=CODIGOS_DESPESAS_COM_PESSOAL,
    ).update(despesa_pessoal=True)


class Migration(migrations.Migration):
    dependencies = [
        ("financeiro", "0060_entrega_contas_receber"),
    ]

    operations = [
        migrations.RunPython(
            corrigir_classificacao,
            restaurar_classificacao_anterior,
        ),
    ]
