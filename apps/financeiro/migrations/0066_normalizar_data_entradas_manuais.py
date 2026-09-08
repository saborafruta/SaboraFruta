from django.db import migrations
from django.db.models import F


def normalizar_entradas_manuais(apps, schema_editor):
    ExtratoBancario = apps.get_model('financeiro', 'ExtratoBancario')
    ContaPagar = apps.get_model('financeiro', 'ContaPagar')
    PagamentoContaPagar = apps.get_model('financeiro', 'PagamentoContaPagar')

    entradas = list(
        ExtratoBancario.objects.filter(origem='manual', valor__gt=0)
        .exclude(data_credito=F('data_lancamento'))
        .values_list('pk', 'data_lancamento')
    )
    for movimento_id, data_lancamento in entradas:
        contas_taxa = ContaPagar.objects.filter(
            documento_tipo='taxa_extrato',
            documento_id=movimento_id,
        )
        PagamentoContaPagar.objects.filter(conta_pagar__in=contas_taxa).update(
            data_pagamento=data_lancamento,
        )
        contas_taxa.update(
            data_emissao=data_lancamento,
            data_vencimento=data_lancamento,
            data_pagamento=data_lancamento,
            data_competencia=data_lancamento.replace(day=1),
        )

    ExtratoBancario.objects.filter(origem='manual', valor__gt=0).update(
        data_credito=F('data_lancamento'),
        prazo_compensacao_aplicado=0,
    )


class Migration(migrations.Migration):
    dependencies = [('financeiro', '0065_classificar_vendas_e_marcar_pdv_entregue')]

    operations = [
        migrations.RunPython(normalizar_entradas_manuais, migrations.RunPython.noop),
    ]
