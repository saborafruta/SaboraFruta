import unicodedata
from decimal import Decimal

from django.db import migrations


def _normalizar(valor):
    return "".join(
        caractere
        for caractere in unicodedata.normalize("NFD", valor or "")
        if unicodedata.category(caractere) != "Mn"
    ).casefold()


def configurar(apps, schema_editor):
    FormaPagamento = apps.get_model("financeiro", "FormaPagamento")
    formas_pix = FormaPagamento.objects.filter(
        empresa__cnpj="50649395000126",
        tipo="pix",
    ).select_related("empresa", "filial", "conta_bancaria_padrao")
    origens = [forma for forma in formas_pix if "orenda" in _normalizar(forma.descricao)]
    for origem in origens:
        existente = FormaPagamento.objects.filter(
            empresa=origem.empresa,
            filial=origem.filial,
            descricao__iexact="PIX ORENDA LINK",
        ).first()
        forma = existente or FormaPagamento(
            empresa=origem.empresa,
            filial=origem.filial,
            descricao="PIX ORENDA LINK",
        )
        forma.tipo = "pix"
        forma.codigo_sefaz = origem.codigo_sefaz
        forma.requer_tef = False
        forma.gera_parcelas = False
        forma.movimenta_caixa = True
        forma.prazo_liquidacao_dias = 0
        forma.prazo_compensacao_dias_uteis = 1
        forma.taxa_administrativa = Decimal("0")
        forma.taxa_fixa = Decimal("0.50")
        forma.conta_bancaria_padrao = origem.conta_bancaria_padrao
        forma.ativo = True
        forma.save()


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0031_ajustar_pix_maquininha_eureka")]
    operations = [migrations.RunPython(configurar, migrations.RunPython.noop)]
