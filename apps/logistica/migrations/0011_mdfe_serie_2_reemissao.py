from django.db import migrations


CNPJ_DESFRUTA = "14004764000160"
STATUS_MUTAVEIS = ("rascunho", "aguardando_nfe", "processando", "rejeitado")


def migrar_mdfes_para_serie_2(apps, schema_editor):
    Filial = apps.get_model("core", "Filial")
    ParametrosSistema = apps.get_model("core", "ParametrosSistema")
    ParametroDocumentoFiscal = apps.get_model("core", "ParametroDocumentoFiscal")
    MDFe = apps.get_model("logistica", "MDFe")
    DocumentoFiscal = apps.get_model("financeiro", "DocumentoFiscal")

    filial = None
    for candidata in Filial.objects.all().only("pk", "cnpj"):
        if "".join(ch for ch in str(candidata.cnpj or "") if ch.isdigit()) == CNPJ_DESFRUTA:
            filial = candidata
            break
    if not filial:
        return

    mdfes = list(
        MDFe.objects.filter(
            filial_id=filial.pk,
            status__in=STATUS_MUTAVEIS,
        ).order_by("numero", "pk")
    )
    for mdfe in mdfes:
        MDFe.objects.filter(pk=mdfe.pk).update(
            serie="2",
            status="rascunho",
            chave_acesso="",
            protocolo_autorizacao="",
            data_autorizacao=None,
            data_cancelamento=None,
            mensagem_sefaz="",
        )
        if mdfe.documento_fiscal_id:
            DocumentoFiscal.objects.filter(pk=mdfe.documento_fiscal_id).update(
                numero=mdfe.numero,
                serie=2,
                status="pendente",
                codigo_status_sefaz="",
                mensagem_sefaz="",
                chave=None,
                protocolo="",
                data_autorizacao=None,
                data_cancelamento=None,
            )

    parametros, _ = ParametrosSistema.objects.get_or_create(filial_id=filial.pk)
    maior_numero = (
        MDFe.objects.filter(filial_id=filial.pk)
        .order_by("-numero")
        .values_list("numero", flat=True)
        .first()
        or 0
    )
    documento_parametros, _ = ParametroDocumentoFiscal.objects.get_or_create(
        parametros_id=parametros.pk,
        tipo_documento="mdfe",
        defaults={
            "habilitado": True,
            "serie": 2,
            "proximo_numero": maior_numero + 1,
        },
    )
    documento_parametros.serie = 2
    documento_parametros.proximo_numero = max(
        documento_parametros.proximo_numero,
        maior_numero + 1,
    )
    documento_parametros.save(update_fields=["serie", "proximo_numero"])


class Migration(migrations.Migration):
    dependencies = [
        ("logistica", "0010_reparar_mdfe_nfe_transferencia"),
    ]

    operations = [
        migrations.RunPython(
            migrar_mdfes_para_serie_2,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
