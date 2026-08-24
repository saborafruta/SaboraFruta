from django.db import migrations


def vincular_contas_bancarias(apps, schema_editor):
    ContaPagar = apps.get_model("financeiro", "ContaPagar")
    PagamentoContaPagar = apps.get_model("financeiro", "PagamentoContaPagar")
    ExtratoBancario = apps.get_model("financeiro", "ExtratoBancario")
    ContaReceber = apps.get_model("financeiro", "ContaReceber")
    PagamentoVendaPDV = apps.get_model("pdv", "PagamentoVendaPDV")

    contas = ContaPagar.objects.filter(documento_tipo__startswith="taxa_")
    for conta in contas.iterator():
        conta_bancaria_id = None
        if conta.documento_tipo == "taxa_extrato":
            conta_bancaria_id = ExtratoBancario.objects.filter(
                pk=conta.documento_id,
            ).values_list("conta_bancaria_id", flat=True).first()
        elif conta.documento_tipo == "taxa_receber":
            conta_bancaria_id = ContaReceber.objects.filter(
                pk=conta.documento_id,
            ).values_list("conta_bancaria_id", flat=True).first()
        elif conta.documento_tipo == "taxa_pdv":
            pagamento = PagamentoVendaPDV.objects.filter(
                pk=conta.documento_id,
            ).select_related("forma_pagamento").first()
            if pagamento:
                conta_bancaria_id = (
                    pagamento.conta_bancaria_id
                    or pagamento.forma_pagamento.conta_bancaria_padrao_id
                )

        if not conta_bancaria_id:
            continue
        ContaPagar.objects.filter(pk=conta.pk).update(
            conta_bancaria_id=conta_bancaria_id,
        )
        PagamentoContaPagar.objects.filter(conta_pagar_id=conta.pk).update(
            conta_bancaria_id=conta_bancaria_id,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("financeiro", "0040_taxas_como_contas_pagas"),
    ]

    operations = [
        migrations.RunPython(vincular_contas_bancarias, migrations.RunPython.noop),
    ]
