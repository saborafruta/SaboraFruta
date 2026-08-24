from decimal import Decimal

from django.db import migrations, models


PREFIXO_LINK = "(LINK DE PAGAMENTO) "


def configurar_e_duplicar(apps, schema_editor):
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    TaxaParcelamento = apps.get_model("financeiro", "TaxaParcelamento")

    for forma in FormaPagamento.objects.filter(ativo=True).iterator():
        descricao = (forma.descricao or "").strip()
        descricao_normalizada = descricao.casefold()

        if descricao_normalizada == "pix (orenda)":
            FormaPagamento.objects.filter(pk=forma.pk).update(
                taxa_administrativa=Decimal("0.99"),
                taxa_fixa=Decimal("0.00"),
                tarifa_pagamento_fixa=Decimal("0.50"),
            )
        elif descricao_normalizada == "boleto":
            FormaPagamento.objects.filter(pk=forma.pk).update(
                taxa_administrativa=Decimal("0.00"),
                taxa_fixa=Decimal("4.50"),
                tarifa_pagamento_fixa=Decimal("0.50"),
            )

    cartoes = FormaPagamento.objects.filter(
        tipo__in=["cartao_debito", "cartao_credito"],
    ).exclude(descricao__startswith=PREFIXO_LINK)
    for origem in cartoes.iterator():
        descricao = f"{PREFIXO_LINK}{origem.descricao}"[:60]
        copia, criada = FormaPagamento.objects.get_or_create(
            empresa_id=origem.empresa_id,
            filial_id=origem.filial_id,
            descricao=descricao,
            defaults={
                "tipo": origem.tipo,
                "codigo_sefaz": origem.codigo_sefaz,
                "requer_tef": origem.requer_tef,
                "gera_parcelas": origem.gera_parcelas,
                "movimenta_caixa": origem.movimenta_caixa,
                "prazo_liquidacao_dias": origem.prazo_liquidacao_dias,
                "prazo_compensacao_dias_uteis": origem.prazo_compensacao_dias_uteis,
                "taxa_administrativa": origem.taxa_administrativa,
                "taxa_fixa": origem.taxa_fixa,
                "tarifa_pagamento_fixa": origem.tarifa_pagamento_fixa,
                "conta_bancaria_padrao_id": origem.conta_bancaria_padrao_id,
                "ativo": origem.ativo,
            },
        )
        if not criada:
            continue
        for taxa in TaxaParcelamento.objects.filter(forma_pagamento_id=origem.pk):
            TaxaParcelamento.objects.create(
                forma_pagamento_id=copia.pk,
                parcelas=taxa.parcelas,
                bandeira=taxa.bandeira,
                taxa=taxa.taxa,
            )


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0043_consolidar_formas_contas_bancarias")]

    operations = [
        migrations.AddField(
            model_name="formapagamento",
            name="tarifa_pagamento_fixa",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Tarifa cobrada pelo banco quando esta forma é usada para pagar.",
                max_digits=14,
            ),
        ),
        migrations.RunPython(configurar_e_duplicar, migrations.RunPython.noop),
    ]
