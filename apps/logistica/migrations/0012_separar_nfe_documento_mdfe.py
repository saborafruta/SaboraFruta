from django.db import migrations
from django.utils import timezone


CHAVE_NFE_TRANSFERENCIA = "24260714004764000160550050000000011827887399"
CNPJ_DESFRUTA = "14004764000160"


def separar_nfe_do_mdfe(apps, schema_editor):
    DocumentoFiscal = apps.get_model("financeiro", "DocumentoFiscal")
    MDFe = apps.get_model("logistica", "MDFe")
    DocumentoMDFe = apps.get_model("logistica", "DocumentoMDFe")

    vinculos = DocumentoMDFe.objects.filter(
        tipo_documento="nfe",
        chave_acesso=CHAVE_NFE_TRANSFERENCIA,
    ).select_related("mdfe", "documento_fiscal")

    for vinculo in vinculos:
        mdfe = vinculo.mdfe
        nfe = vinculo.documento_fiscal
        if not nfe:
            continue
        documento_compartilhado = mdfe.documento_fiscal_id == nfe.pk

        # Restaura a NF-e de transferencia comprovadamente autorizada. A migracao
        # anterior limpou este registro ao confundi-lo com o documento do MDF-e.
        numero_nfe = int(vinculo.numero_documento or 1)
        serie_nfe = int(vinculo.serie or 5)
        nfe_canonica = (
            DocumentoFiscal.objects.filter(chave=CHAVE_NFE_TRANSFERENCIA)
            .exclude(pk=nfe.pk)
            .first()
        )
        if nfe_canonica:
            vinculo.documento_fiscal_id = nfe_canonica.pk
            vinculo.save(update_fields=["documento_fiscal"])
            nfe = nfe_canonica
        else:
            nfe.tipo_documento = "nfe"
            nfe.numero = numero_nfe
            nfe.serie = serie_nfe
            nfe.chave = CHAVE_NFE_TRANSFERENCIA
            nfe.status = "autorizada"
            nfe.codigo_status_sefaz = "100"
            nfe.mensagem_sefaz = "Autorizado o uso da NF-e"
            if not nfe.data_autorizacao:
                nfe.data_autorizacao = nfe.updated_at or timezone.now()
            nfe.save(
                update_fields=[
                    "tipo_documento",
                    "numero",
                    "serie",
                    "chave",
                    "status",
                    "codigo_status_sefaz",
                    "mensagem_sefaz",
                    "data_autorizacao",
                    "updated_at",
                ]
            )

        if not documento_compartilhado:
            continue

        # Desvincula primeiro para preservar a NF-e e cria um DocumentoFiscal
        # exclusivo para a emissao do MDF-e.
        MDFe.objects.filter(pk=mdfe.pk).update(documento_fiscal_id=None)
        documento_mdfe = (
            DocumentoFiscal.objects.filter(
                origem_tipo="mdfe",
                origem_id=mdfe.pk,
                tipo_documento="mdfe",
            )
            .exclude(pk=nfe.pk)
            .first()
        )
        if not documento_mdfe:
            documento_mdfe = DocumentoFiscal.objects.create(
                filial_id=mdfe.filial_id,
                tipo_documento="mdfe",
                origem_tipo="mdfe",
                origem_id=mdfe.pk,
                numero=mdfe.numero,
                serie=2,
                natureza_operacao_descricao="Manifesto de documentos fiscais",
                tipo_operacao="1",
                emitente_cnpj=CNPJ_DESFRUTA,
                destinatario_snapshot={},
                valor_total=mdfe.valor_total,
                status="pendente",
                data_emissao=timezone.now(),
                usuario_id=mdfe.responsavel_id,
            )

        MDFe.objects.filter(pk=mdfe.pk).update(
            documento_fiscal_id=documento_mdfe.pk,
            serie="2",
            status="rascunho",
            chave_acesso="",
            protocolo_autorizacao="",
            data_autorizacao=None,
            data_cancelamento=None,
            mensagem_sefaz="",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("logistica", "0011_mdfe_serie_2_reemissao"),
    ]

    operations = [
        migrations.RunPython(
            separar_nfe_do_mdfe,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
