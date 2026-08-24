from datetime import date
from decimal import Decimal

from django.db import migrations
from django.db.models import Max, Q


ZERO = Decimal("0")


def _categoria_taxas(apps, empresa):
    PlanoContabil = apps.get_model("financeiro", "PlanoContabil")
    PlanoContas = apps.get_model("financeiro", "PlanoContas")

    conta = PlanoContabil.objects.filter(
        empresa=empresa, descricao__iexact="Taxas por transacao",
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
            data_inicio=date(2026, 8, 23), nivel=(pai.nivel + 1) if pai else 1,
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
    return categoria, conta


def _registrar(apps, origem, item, filial, data_liquidacao, valor_taxa, forma_id):
    if not data_liquidacao or not valor_taxa or valor_taxa <= ZERO:
        return
    ContaPagar = apps.get_model("financeiro", "ContaPagar")
    PagamentoContaPagar = apps.get_model("financeiro", "PagamentoContaPagar")
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    forma = FormaPagamento.objects.filter(pk=forma_id).first() if forma_id else None
    categoria, conta_contabil = _categoria_taxas(apps, filial.empresa)
    descricao_forma = forma.descricao if forma else "recebimento"
    conta, _ = ContaPagar.objects.update_or_create(
        filial=filial, documento_tipo=f"taxa_{origem}", documento_id=item.pk,
        defaults={
            "descricao_despesa": f"Taxa por transacao - {descricao_forma}",
            "documento_numero": f"TX-{item.pk}"[:20],
            "valor_original": valor_taxa, "valor_final": valor_taxa,
            "valor_pago": valor_taxa, "valor_saldo": ZERO,
            "data_emissao": data_liquidacao, "data_vencimento": data_liquidacao,
            "data_pagamento": data_liquidacao,
            "data_competencia": data_liquidacao.replace(day=1),
            "forma_pagamento": forma, "forma_pagamento_prevista": forma,
            "conta_bancaria": None, "plano_contas": categoria,
            "conta_contabil": conta_contabil, "status": "pago",
            "observacao": "Taxa retida automaticamente na liquidacao do recebimento.",
        },
    )
    PagamentoContaPagar.objects.update_or_create(
        conta_pagar=conta,
        defaults={
            "filial": filial, "data_pagamento": data_liquidacao,
            "valor_pago": valor_taxa, "forma_pagamento": forma,
            "conta_bancaria": None,
            "referencia_pagamento": "Retida na liquidacao do recebimento",
            "observacao": "Sem segunda movimentacao bancaria; o credito ja entrou liquido.",
        },
    )


def criar_contas_pagas_taxas(apps, schema_editor):
    Extrato = apps.get_model("financeiro", "ExtratoBancario")
    Receber = apps.get_model("financeiro", "ContaReceber")
    PagamentoVenda = apps.get_model("pdv", "PagamentoVendaPDV")

    for item in Extrato.objects.filter(valor_taxa__gt=ZERO).select_related("filial__empresa"):
        if item.valor > ZERO:
            _registrar(
                apps, "extrato", item, item.filial,
                item.data_credito or item.data_lancamento,
                item.valor_taxa, item.forma_pagamento_id,
            )
    for item in Receber.objects.filter(valor_taxa_recebimento__gt=ZERO).select_related("filial__empresa"):
        _registrar(
            apps, "receber", item, item.filial,
            item.data_liquidacao_prevista or item.data_pagamento,
            item.valor_taxa_recebimento, item.forma_pagamento_id,
        )
    for item in PagamentoVenda.objects.filter(valor_taxa__gt=ZERO).select_related("venda_pdv__filial__empresa"):
        _registrar(
            apps, "pdv", item, item.venda_pdv.filial,
            item.data_liquidacao_prevista, item.valor_taxa, item.forma_pagamento_id,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("financeiro", "0039_dados_cartao_recebimentos"),
        ("pdv", "0013_pagamentovendapdv_data_liquidacao_prevista_and_more"),
    ]

    operations = [
        migrations.RunPython(criar_contas_pagas_taxas, migrations.RunPython.noop),
    ]
