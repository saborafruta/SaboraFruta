from datetime import date
from decimal import Decimal

from django.db import migrations
from django.db.models import Max, Q


ZERO = Decimal("0")


def _categoria_taxas(apps, empresa):
    PlanoContabil = apps.get_model("financeiro", "PlanoContabil")
    PlanoContas = apps.get_model("financeiro", "PlanoContas")

    conta = PlanoContabil.objects.filter(
        empresa=empresa,
    ).filter(
        Q(descricao__iexact="Taxas por transacao")
        | Q(classificacao="ITED.TAXAS.TRANSACAO")
    ).first()
    if not conta:
        pai = PlanoContabil.objects.filter(
            empresa=empresa, tipo_conta="S",
        ).filter(
            Q(descricao__icontains="financeir") | Q(descricao__icontains="despesa")
        ).order_by("-nivel", "ordem").first()
        maximos = PlanoContabil.objects.filter(empresa=empresa).aggregate(
            codigo=Max("codigo_referencia"), ordem=Max("ordem"),
        )
        conta = PlanoContabil.objects.create(
            empresa=empresa, conta_pai=pai,
            codigo_referencia=(maximos["codigo"] or 0) + 1,
            classificacao="ITED.TAXAS.TRANSACAO", tipo_conta="A",
            descricao="Taxas por transacao", codigo_dre=getattr(pai, "codigo_dre", "") or "",
            data_inicio=date(2026, 8, 25), nivel=(pai.nivel + 1) if pai else 1,
            ordem=(maximos["ordem"] or 0) + 1,
            origem="Classificacao automatica - iTed", ativo=True,
        )

    grupo = PlanoContas.objects.filter(
        empresa=empresa, tipo="D", nivel=1, descricao__iexact="Despesas financeiras",
    ).first() or PlanoContas.objects.create(
        empresa=empresa, codigo="399", descricao="Despesas financeiras",
        tipo="D", nivel=1, aceita_lancamento=False, ativo=True,
    )
    tipo = PlanoContas.objects.filter(
        empresa=empresa, tipo="D", nivel=2, conta_pai=grupo,
        descricao__iexact="Taxas e tarifas",
    ).first() or PlanoContas.objects.create(
        empresa=empresa, conta_pai=grupo, codigo="39901", descricao="Taxas e tarifas",
        tipo="D", nivel=2, aceita_lancamento=False, ativo=True,
    )
    categoria = PlanoContas.objects.filter(
        empresa=empresa, tipo="D", nivel=3, conta_pai=tipo,
        descricao__iexact="Taxas por transacao",
    ).first()
    if not categoria:
        categoria = PlanoContas.objects.create(
            empresa=empresa, conta_pai=tipo, conta_contabil=conta,
            codigo="3990100001", descricao="Taxas por transacao", tipo="D",
            nivel=3, aceita_lancamento=True, ativo=True,
        )
    elif categoria.conta_contabil_id != conta.pk:
        categoria.conta_contabil = conta
        categoria.save(update_fields=["conta_contabil"])
    return categoria, conta


def criar_tarifas_pagamento(apps, schema_editor):
    ContaPagar = apps.get_model("financeiro", "ContaPagar")
    PagamentoContaPagar = apps.get_model("financeiro", "PagamentoContaPagar")

    pagamentos = (
        PagamentoContaPagar.objects
        .filter(
            forma_pagamento__tarifa_pagamento_fixa__gt=ZERO,
        )
        .select_related(
            "filial__empresa", "forma_pagamento", "conta_bancaria",
            "conta_pagar",
        )
    )
    pagamentos = pagamentos.exclude(conta_pagar__documento_tipo__startswith="taxa_")
    for pagamento in pagamentos.iterator():
        valor = pagamento.forma_pagamento.tarifa_pagamento_fixa or ZERO
        if valor <= ZERO:
            continue
        categoria, conta_contabil = _categoria_taxas(apps, pagamento.filial.empresa)
        conta_bancaria = pagamento.conta_bancaria or pagamento.forma_pagamento.conta_bancaria_padrao
        conta, _ = ContaPagar.objects.update_or_create(
            filial=pagamento.filial,
            documento_tipo="taxa_pagamento",
            documento_id=pagamento.pk,
            defaults={
                "descricao_despesa": f"Tarifa de pagamento - {pagamento.forma_pagamento.descricao}",
                "documento_numero": f"TX-{pagamento.pk}"[:20],
                "valor_original": valor,
                "valor_final": valor,
                "valor_pago": valor,
                "valor_saldo": ZERO,
                "data_emissao": pagamento.data_pagamento,
                "data_vencimento": pagamento.data_pagamento,
                "data_pagamento": pagamento.data_pagamento,
                "data_competencia": pagamento.data_pagamento.replace(day=1),
                "forma_pagamento": pagamento.forma_pagamento,
                "forma_pagamento_prevista": pagamento.forma_pagamento,
                "conta_bancaria": conta_bancaria,
                "plano_contas": categoria,
                "conta_contabil": conta_contabil,
                "status": "pago",
                "observacao": "Tarifa cobrada automaticamente pelo banco ao realizar o pagamento.",
                "excluido_em": None,
                "excluido_por": None,
                "motivo_exclusao": "",
            },
        )
        PagamentoContaPagar.objects.update_or_create(
            conta_pagar=conta,
            defaults={
                "filial": pagamento.filial,
                "data_pagamento": pagamento.data_pagamento,
                "valor_pago": valor,
                "forma_pagamento": pagamento.forma_pagamento,
                "conta_bancaria": conta_bancaria,
                "referencia_pagamento": "Tarifa bancaria do pagamento",
                "observacao": "Tarifa cobrada automaticamente pelo banco ao realizar o pagamento.",
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("financeiro", "0051_alter_contapagar_status"),
    ]

    operations = [
        migrations.RunPython(criar_tarifas_pagamento, migrations.RunPython.noop),
    ]
