import unicodedata
from decimal import Decimal

from django.db import migrations


TAXAS_DEBITO = {
    "mastercard": "3.22", "visa": "1.11", "elo": "3.97", "amex": "3.97", "hiper": "3.97",
}
TAXAS_CREDITO = {
    1: {"mastercard": "3.20", "visa": "1.10", "elo": "4.05", "amex": "4.05", "hiper": "4.05"},
    2: {"mastercard": "4.46", "visa": "4.46", "elo": "4.90", "amex": "4.90", "hiper": "4.90"},
    3: {"mastercard": "5.29", "visa": "5.29", "elo": "5.61", "amex": "5.61", "hiper": "5.61"},
    4: {"mastercard": "7.06", "visa": "7.06", "elo": "6.41", "amex": "6.41", "hiper": "6.41"},
    5: {"mastercard": "8.03", "visa": "8.03", "elo": "7.45", "amex": "7.45", "hiper": "7.45"},
    6: {"mastercard": "9.20", "visa": "9.20", "elo": "8.50", "amex": "8.50", "hiper": "8.50"},
}


def _normalizar(valor):
    return "".join(
        c for c in unicodedata.normalize("NFD", valor or "")
        if unicodedata.category(c) != "Mn"
    ).casefold()


def configurar(apps, schema_editor):
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    TaxaParcelamento = apps.get_model("financeiro", "TaxaParcelamento")
    formas = FormaPagamento.objects.filter(empresa__cnpj="50649395000126")
    for forma in formas:
        texto = _normalizar(f"{forma.descricao} {forma.tipo}")
        if forma.tipo == "pix" or "pix" in texto:
            forma.taxa_administrativa = Decimal("0.99")
            forma.prazo_compensacao_dias_uteis = 1
            forma.save(update_fields=["taxa_administrativa", "prazo_compensacao_dias_uteis"])
            continue
        if forma.tipo == "boleto":
            forma.prazo_compensacao_dias_uteis = 1
            forma.save(update_fields=["prazo_compensacao_dias_uteis"])
            continue
        if forma.tipo == "cartao_debito":
            forma.prazo_compensacao_dias_uteis = 1
            forma.save(update_fields=["prazo_compensacao_dias_uteis"])
            for bandeira, taxa in TAXAS_DEBITO.items():
                TaxaParcelamento.objects.update_or_create(
                    forma_pagamento=forma, parcelas=1, bandeira=bandeira,
                    defaults={"taxa": Decimal(taxa)},
                )
            continue
        if forma.tipo == "cartao_credito":
            forma.prazo_compensacao_dias_uteis = 1
            forma.save(update_fields=["prazo_compensacao_dias_uteis"])
            for parcelas, taxas in TAXAS_CREDITO.items():
                for bandeira, taxa in taxas.items():
                    TaxaParcelamento.objects.update_or_create(
                        forma_pagamento=forma, parcelas=parcelas, bandeira=bandeira,
                        defaults={"taxa": Decimal(taxa)},
                    )


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0028_alter_taxaparcelamento_options_and_more")]
    operations = [migrations.RunPython(configurar, migrations.RunPython.noop)]
