from decimal import Decimal

from django.db import migrations


CHAVE_NFE_TRANSFERENCIA = "24260714004764000160550050000000011827887399"


def reparar_mdfe_transferencia(apps, schema_editor):
    DocumentoFiscal = apps.get_model("financeiro", "DocumentoFiscal")
    MDFe = apps.get_model("logistica", "MDFe")
    DocumentoMDFe = apps.get_model("logistica", "DocumentoMDFe")

    nfe = DocumentoFiscal.objects.filter(
        chave=CHAVE_NFE_TRANSFERENCIA,
        tipo_documento="nfe",
        status="autorizada",
    ).first()
    if not nfe:
        return

    mdfe = (
        MDFe.objects.filter(
            filial_id=nfe.filial_id,
            numero=1,
            status__in=["rascunho", "aguardando_nfe"],
        )
        .order_by("pk")
        .first()
    )
    if not mdfe or DocumentoMDFe.objects.filter(mdfe_id=mdfe.pk).exists():
        return

    peso = Decimal("5.400")
    valor = nfe.valor_total or Decimal("52.50")
    DocumentoMDFe.objects.create(
        mdfe_id=mdfe.pk,
        documento_fiscal_id=nfe.pk,
        tipo_documento="nfe",
        chave_acesso=CHAVE_NFE_TRANSFERENCIA,
        numero_documento=str(nfe.numero),
        serie=str(nfe.serie),
        emitente_nome="DESFRUTA",
        emitente_documento="14004764000160",
        municipio_descarga="Natal",
        uf_descarga="RN",
        peso_kg=peso,
        valor=valor,
        observacao="NF-e de transferência vinculada automaticamente.",
    )
    MDFe.objects.filter(pk=mdfe.pk).update(
        uf_carregamento="RN",
        municipio_carregamento="Macaíba",
        codigo_municipio_carregamento="2407104",
        uf_descarregamento="RN",
        municipio_descarregamento="Natal",
        codigo_municipio_descarregamento="2408102",
        qtd_nfes=1,
        peso_total_kg=peso,
        valor_total=valor,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("logistica", "0009_mdfe_datas_viagem"),
    ]

    operations = [
        migrations.RunPython(
            reparar_mdfe_transferencia,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
