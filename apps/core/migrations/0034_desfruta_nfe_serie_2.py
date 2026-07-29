from django.db import migrations


DESFRUTA_CNPJ = "14004764000160"


def configurar_serie_nfe_desfruta(apps, schema_editor):
    Filial = apps.get_model("core", "Filial")
    ParametroDocumentoFiscal = apps.get_model(
        "core", "ParametroDocumentoFiscal"
    )

    filial = Filial.objects.filter(cnpj=DESFRUTA_CNPJ).first()
    if filial is None:
        return

    Filial.objects.filter(pk=filial.pk).update(
        serie_nfe=2,
        proximo_numero_nfe=1,
    )
    ParametroDocumentoFiscal.objects.filter(
        parametros__filial_id=filial.pk,
        tipo_documento="nfe",
    ).update(
        serie=2,
        proximo_numero=1,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0033_permissao_modulo_crm"),
    ]

    operations = [
        migrations.RunPython(
            configurar_serie_nfe_desfruta,
            migrations.RunPython.noop,
        ),
    ]
