from decimal import Decimal

from django.db import migrations


CNPJ_EUREKA = "50649395000126"
PREFIXO_LINK = "(LINK DE PAGAMENTO) "


def _tem_referencia_forma(forma, models):
    return any(
        model.objects.filter(**{field: forma}).exists()
        for model, field in models
    )


def _tem_referencia_categoria(categoria, models):
    return any(
        model.objects.filter(**{field: categoria}).exists()
        for model, field in models
    )


def limpar_configuracoes_fora_eureka(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    TaxaParcelamento = apps.get_model("financeiro", "TaxaParcelamento")
    PlanoContas = apps.get_model("financeiro", "PlanoContas")
    ContaPagar = apps.get_model("financeiro", "ContaPagar")
    ContaReceber = apps.get_model("financeiro", "ContaReceber")
    ExtratoBancario = apps.get_model("financeiro", "ExtratoBancario")
    PagamentoContaPagar = apps.get_model("financeiro", "PagamentoContaPagar")
    PagamentoContaReceber = apps.get_model("financeiro", "PagamentoContaReceber")
    PagamentoVendaPDV = apps.get_model("pdv", "PagamentoVendaPDV")

    referencias_forma = (
        (ContaReceber, "forma_pagamento"),
        (ContaPagar, "forma_pagamento"),
        (ContaPagar, "forma_pagamento_prevista"),
        (PagamentoContaPagar, "forma_pagamento"),
        (PagamentoContaReceber, "forma_pagamento"),
        (PagamentoVendaPDV, "forma_pagamento"),
        (ExtratoBancario, "forma_pagamento"),
    )
    referencias_categoria = (
        (ContaPagar, "plano_contas"),
        (ContaReceber, "plano_contas"),
        (ExtratoBancario, "plano_contas"),
    )

    empresas = Empresa.objects.exclude(cnpj=CNPJ_EUREKA)
    for empresa in empresas.iterator():
        FormaPagamento.objects.filter(
            empresa=empresa,
            descricao__iexact="BOLETO",
        ).update(
            taxa_administrativa=Decimal("0.00"),
            taxa_fixa=Decimal("0.00"),
            tarifa_pagamento_fixa=Decimal("0.00"),
        )

        for forma in FormaPagamento.objects.filter(
            empresa=empresa,
            descricao__startswith=PREFIXO_LINK,
        ).iterator():
            if _tem_referencia_forma(forma, referencias_forma):
                FormaPagamento.objects.filter(pk=forma.pk).update(ativo=False)
                continue
            TaxaParcelamento.objects.filter(forma_pagamento=forma).delete()
            forma.delete()

        categorias = PlanoContas.objects.filter(
            empresa=empresa,
            descricao__in=("Insumos", "Mercadorias e Insumos"),
        ).order_by("-nivel")
        for categoria in categorias.iterator():
            if (
                PlanoContas.objects.filter(conta_pai=categoria).exists()
                or _tem_referencia_categoria(categoria, referencias_categoria)
            ):
                continue
            categoria.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("financeiro", "0052_tarifas_pagamento_contas_pagas"),
        ("pdv", "0014_corrigir_liquidacao_d0_recente"),
    ]

    operations = [
        migrations.RunPython(
            limpar_configuracoes_fora_eureka,
            migrations.RunPython.noop,
        ),
    ]
