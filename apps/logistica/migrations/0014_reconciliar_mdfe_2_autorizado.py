from datetime import datetime
from decimal import Decimal

from django.db import migrations


CNPJ_DESFRUTA = "14004764000160"
CHAVE_MDFE = "24260714004764000160580010000000021713190294"
PROTOCOLO = "924260003004634"
AUTORIZADO_EM = datetime.fromisoformat("2026-07-30T10:46:30-03:00")


def reconciliar_mdfe_autorizado(apps, schema_editor):
    Filial = apps.get_model("core", "Filial")
    ParametrosSistema = apps.get_model("core", "ParametrosSistema")
    ParametroDocumentoFiscal = apps.get_model("core", "ParametroDocumentoFiscal")
    DocumentoFiscal = apps.get_model("financeiro", "DocumentoFiscal")
    MDFe = apps.get_model("logistica", "MDFe")

    filial = next(
        (
            item
            for item in Filial.objects.all().only("pk", "cnpj")
            if "".join(ch for ch in str(item.cnpj or "") if ch.isdigit())
            == CNPJ_DESFRUTA
        ),
        None,
    )
    if not filial:
        return

    mdfe = MDFe.objects.filter(filial_id=filial.pk, numero=2).first()
    if not mdfe:
        return

    documento = DocumentoFiscal.objects.filter(chave=CHAVE_MDFE).first()
    if not documento and mdfe.documento_fiscal_id:
        documento = DocumentoFiscal.objects.filter(
            pk=mdfe.documento_fiscal_id
        ).first()
    if not documento:
        documento = (
            DocumentoFiscal.objects.filter(
                filial_id=filial.pk,
                tipo_documento="mdfe",
                numero=2,
            )
            .order_by("-pk")
            .first()
        )

    if documento:
        documento.numero = 2
        documento.serie = 1
        documento.chave = CHAVE_MDFE
        documento.status = "autorizada"
        documento.codigo_status_sefaz = "100"
        documento.mensagem_sefaz = "100 - Autorizado o uso do MDF-e"
        documento.protocolo = PROTOCOLO
        documento.data_autorizacao = AUTORIZADO_EM
        documento.valor_produtos = Decimal("1492.75")
        documento.valor_total = Decimal("1492.75")
        documento.save()
        mdfe.documento_fiscal_id = documento.pk

    mdfe.serie = "1"
    mdfe.status = "autorizado"
    mdfe.chave_acesso = CHAVE_MDFE
    mdfe.protocolo_autorizacao = PROTOCOLO
    mdfe.data_autorizacao = AUTORIZADO_EM
    mdfe.mensagem_sefaz = "100 - Autorizado o uso do MDF-e"
    mdfe.peso_total_kg = Decimal("249.000")
    mdfe.valor_total = Decimal("1492.75")
    mdfe.save()

    parametros, _ = ParametrosSistema.objects.get_or_create(filial_id=filial.pk)
    configuracao, _ = ParametroDocumentoFiscal.objects.get_or_create(
        parametros_id=parametros.pk,
        tipo_documento="mdfe",
        defaults={"habilitado": True, "serie": 1, "proximo_numero": 3},
    )
    configuracao.serie = 1
    configuracao.proximo_numero = max(configuracao.proximo_numero or 1, 3)
    configuracao.save(update_fields=["serie", "proximo_numero"])


class Migration(migrations.Migration):
    dependencies = [
        ("logistica", "0013_mdfe_serie_1_proximas_emissoes"),
    ]

    operations = [
        migrations.RunPython(
            reconciliar_mdfe_autorizado,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
