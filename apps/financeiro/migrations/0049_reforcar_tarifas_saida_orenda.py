from decimal import Decimal

from django.db import migrations


def reforcar_tarifas_saida(apps, schema_editor):
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")

    for forma in FormaPagamento.objects.all().iterator():
        descricao = " ".join((forma.descricao or "").casefold().split())
        descricao_sem_parenteses = descricao.replace("(", "").replace(")", "")
        if descricao_sem_parenteses in {"pix orenda", "boleto"}:
            FormaPagamento.objects.filter(pk=forma.pk).update(
                tarifa_pagamento_fixa=Decimal("0.50"),
            )


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0048_criar_categoria_insumos")]

    operations = [
        migrations.RunPython(reforcar_tarifas_saida, migrations.RunPython.noop),
    ]
