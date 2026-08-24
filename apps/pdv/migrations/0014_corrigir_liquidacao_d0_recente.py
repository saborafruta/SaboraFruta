from datetime import date

from django.db import migrations
from django.utils import timezone


def corrigir_liquidacao_d0_recente(apps, schema_editor):
    PagamentoVendaPDV = apps.get_model("pdv", "PagamentoVendaPDV")
    ContaPagar = apps.get_model("financeiro", "ContaPagar")
    PagamentoContaPagar = apps.get_model("financeiro", "PagamentoContaPagar")

    pagamentos = PagamentoVendaPDV.objects.filter(
        venda_pdv__data_venda__date__gte=date(2026, 8, 24),
        forma_pagamento__prazo_compensacao_dias_uteis=0,
        prazo_compensacao_aplicado__gt=0,
    ).select_related("venda_pdv")
    for pagamento in pagamentos.iterator():
        data_liquidacao = timezone.localtime(pagamento.venda_pdv.data_venda).date()
        PagamentoVendaPDV.objects.filter(pk=pagamento.pk).update(
            prazo_compensacao_aplicado=0,
            data_liquidacao_prevista=data_liquidacao,
        )
        taxas = ContaPagar.objects.filter(
            filial_id=pagamento.venda_pdv.filial_id,
            documento_tipo="taxa_pdv",
            documento_id=pagamento.pk,
        )
        taxas.update(
            data_emissao=data_liquidacao,
            data_vencimento=data_liquidacao,
            data_pagamento=data_liquidacao,
            data_competencia=data_liquidacao.replace(day=1),
        )
        PagamentoContaPagar.objects.filter(conta_pagar__in=taxas).update(
            data_pagamento=data_liquidacao,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("financeiro", "0042_vincular_formas_as_contas_por_nome"),
        ("pdv", "0013_pagamentovendapdv_data_liquidacao_prevista_and_more"),
    ]
    operations = [
        migrations.RunPython(corrigir_liquidacao_d0_recente, migrations.RunPython.noop),
    ]
