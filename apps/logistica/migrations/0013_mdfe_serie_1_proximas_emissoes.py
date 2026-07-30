from django.db import migrations


CNPJ_DESFRUTA = "14004764000160"


def configurar_proximos_mdfes_na_serie_1(apps, schema_editor):
    Filial = apps.get_model("core", "Filial")
    ParametrosSistema = apps.get_model("core", "ParametrosSistema")
    ParametroDocumentoFiscal = apps.get_model("core", "ParametroDocumentoFiscal")
    MDFe = apps.get_model("logistica", "MDFe")
    DocumentoFiscal = apps.get_model("financeiro", "DocumentoFiscal")

    filial = None
    for candidata in Filial.objects.all().only("pk", "cnpj"):
        cnpj = "".join(ch for ch in str(candidata.cnpj or "") if ch.isdigit())
        if cnpj == CNPJ_DESFRUTA:
            filial = candidata
            break
    if not filial:
        return

    maior_mdfe = (
        MDFe.objects.filter(filial_id=filial.pk, serie="1")
        .order_by("-numero")
        .values_list("numero", flat=True)
        .first()
        or 0
    )
    maior_documento = (
        DocumentoFiscal.objects.filter(
            filial_id=filial.pk,
            tipo_documento="mdfe",
            serie=1,
        )
        .order_by("-numero")
        .values_list("numero", flat=True)
        .first()
        or 0
    )
    proximo_numero = max(maior_mdfe, maior_documento, 2) + 1

    parametros, _ = ParametrosSistema.objects.get_or_create(filial_id=filial.pk)
    configuracao, _ = ParametroDocumentoFiscal.objects.get_or_create(
        parametros_id=parametros.pk,
        tipo_documento="mdfe",
        defaults={
            "habilitado": True,
            "serie": 1,
            "proximo_numero": proximo_numero,
        },
    )
    configuracao.serie = 1
    configuracao.proximo_numero = proximo_numero
    configuracao.save(update_fields=["serie", "proximo_numero"])


class Migration(migrations.Migration):
    dependencies = [
        ("logistica", "0012_separar_nfe_documento_mdfe"),
    ]

    operations = [
        migrations.RunPython(
            configurar_proximos_mdfes_na_serie_1,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
