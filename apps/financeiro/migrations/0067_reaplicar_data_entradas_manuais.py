from django.db import migrations
from django.db.models import F


def reaplicar_data_entradas_manuais(apps, schema_editor):
    ExtratoBancario = apps.get_model('financeiro', 'ExtratoBancario')
    ContaPagar = apps.get_model('financeiro', 'ContaPagar')
    PagamentoContaPagar = apps.get_model('financeiro', 'PagamentoContaPagar')
    banco = schema_editor.connection.alias

    entradas = list(
        ExtratoBancario.objects.using(banco).filter(
            origem='manual', valor__gt=0,
        ).exclude(
            data_credito=F('data_lancamento'),
        ).values_list('pk', 'data_lancamento')
    )
    for movimento_id, data_lancamento in entradas:
        contas_taxa = ContaPagar.objects.using(banco).filter(
            documento_tipo='taxa_extrato',
            documento_id=movimento_id,
        )
        PagamentoContaPagar.objects.using(banco).filter(
            conta_pagar__in=contas_taxa,
        ).update(data_pagamento=data_lancamento)
        contas_taxa.update(
            data_emissao=data_lancamento,
            data_vencimento=data_lancamento,
            data_pagamento=data_lancamento,
            data_competencia=data_lancamento.replace(day=1),
        )

    ExtratoBancario.objects.using(banco).filter(
        origem='manual', valor__gt=0,
    ).update(
        data_credito=F('data_lancamento'),
        prazo_compensacao_aplicado=0,
    )


class Migration(migrations.Migration):
    dependencies = [('financeiro', '0066_normalizar_data_entradas_manuais')]

    operations = [
        migrations.RunPython(
            reaplicar_data_entradas_manuais,
            migrations.RunPython.noop,
        ),
    ]
