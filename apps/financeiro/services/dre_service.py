"""Serviço de consolidação do DRE."""
from datetime import date, timedelta
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from apps.financeiro.models import DREConsolidado, ContaReceber, ContaPagar
from apps.produtos.models import LinhaProducao


def consolidar_dre_diario():
    """Recalcula o DRE do mês corrente para todas as filiais e linhas."""
    hoje = timezone.localdate()
    competencia = hoje.replace(day=1)
    from apps.core.models import Filial
    for filial in Filial.objects.filter(ativo=True):
        consolidar_filial(filial, competencia)
        for linha in LinhaProducao.objects.filter(empresa=filial.empresa, ativo=True):
            consolidar_filial(filial, competencia, linha)
    return "ok"


@transaction.atomic
def consolidar_filial(filial, competencia: date, linha_producao=None):
    fim_mes = (competencia + timedelta(days=32)).replace(day=1)

    receita = (
        ContaReceber.objects.filter(
            filial=filial, status="pago",
            data_pagamento__gte=competencia, data_pagamento__lt=fim_mes,
        )
        .aggregate(total=Sum("valor_pago"))
    )
    despesas = (
        ContaPagar.objects.filter(
            filial=filial, status="pago",
            data_pagamento__gte=competencia, data_pagamento__lt=fim_mes,
        )
        .aggregate(total=Sum("valor_pago"))
    )

    receita_bruta = receita["total"] or 0
    despesas_op = despesas["total"] or 0

    dre, _ = DREConsolidado.objects.update_or_create(
        filial=filial, competencia=competencia, linha_producao=linha_producao,
        defaults={
            "receita_bruta": receita_bruta,
            "receita_liquida": receita_bruta,
            "despesas_operacionais": despesas_op,
            "lucro_bruto": receita_bruta,
            "ebitda": receita_bruta - despesas_op,
            "lucro_operacional": receita_bruta - despesas_op,
            "lucro_liquido": receita_bruta - despesas_op,
            "margem_bruta_percentual": 100 if receita_bruta else 0,
            "margem_ebitda_percentual": (
                ((receita_bruta - despesas_op) / receita_bruta * 100) if receita_bruta else 0
            ),
        },
    )
    return dre
