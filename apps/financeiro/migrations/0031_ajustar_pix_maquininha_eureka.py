import unicodedata
from decimal import Decimal

from django.db import migrations


def _normalizar(valor):
    return "".join(
        caractere
        for caractere in unicodedata.normalize("NFD", valor or "")
        if unicodedata.category(caractere) != "Mn"
    ).casefold()


def ajustar(apps, schema_editor):
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    formas = FormaPagamento.objects.filter(
        empresa__cnpj="50649395000126",
        tipo="pix",
    )
    for forma in formas:
        descricao = _normalizar(forma.descricao)
        if "orenda" in descricao or "maquininha" in descricao:
            forma.taxa_administrativa = Decimal("0.99")
            forma.prazo_compensacao_dias_uteis = 1
        elif "karla" in descricao and forma.taxa_administrativa == Decimal("0.99"):
            forma.taxa_administrativa = Decimal("0")
            forma.prazo_compensacao_dias_uteis = 0
        else:
            continue
        forma.save(update_fields=["taxa_administrativa", "prazo_compensacao_dias_uteis"])


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0030_compensacao_conta_receber")]
    operations = [migrations.RunPython(ajustar, migrations.RunPython.noop)]
