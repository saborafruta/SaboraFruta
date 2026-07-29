import io
import zipfile
from datetime import date

from django.test import SimpleTestCase

from apps.core.services.exceptions import DomainError
from apps.fiscal.services.focusnfe_backup_service import (
    BackupFocus,
    FocusNFeBackupService,
    meses_entre,
)


class FakeBackupsResource:
    def __init__(self, listagem=None, arquivos=None):
        self.listagem = listagem or []
        self.arquivos = arquivos or {}
        self.cnpj_consultado = ""

    def listar(self, cnpj):
        self.cnpj_consultado = cnpj
        return self.listagem

    def baixar_xmls(self, url):
        return self.arquivos[url]


class FakeClient:
    def __init__(self, backups):
        self.backups = backups


def criar_zip(arquivos):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as pacote:
        for nome, conteudo in arquivos.items():
            pacote.writestr(nome, conteudo)
    return buffer.getvalue()


class FocusNFeBackupServiceTests(SimpleTestCase):
    def test_lista_somente_backups_validos_e_ordena_por_mes(self):
        resource = FakeBackupsResource(listagem=[
            {"mes": "202607", "xmls": "https://focus.test/202607.zip"},
            {"mes": "invalido", "xmls": "https://focus.test/invalido.zip"},
            {"mes": "202606", "xmls": "https://focus.test/202606.zip"},
            {"mes": "202605", "danfes": "https://focus.test/danfe.zip"},
        ])
        service = FocusNFeBackupService(FakeClient(resource))

        backups = service.listar("14.004.764/0001-60")

        self.assertEqual(resource.cnpj_consultado, "14004764000160")
        self.assertEqual([backup.mes for backup in backups], ["202606", "202607"])

    def test_seleciona_meses_e_le_xmls_em_pastas_internas(self):
        url_junho = "https://focus.test/202606.zip"
        url_julho = "https://focus.test/202607.zip"
        resource = FakeBackupsResource(
            listagem=[
                {"mes": "202606", "xmls": url_junho},
                {"mes": "202607", "xmls": url_julho},
            ],
            arquivos={
                url_julho: criar_zip({
                    "XMLs/nota.xml": '<?xml version="1.0"?><nfeProc />',
                    "XMLs/leia-me.txt": "ignorar",
                }),
            },
        )
        service = FocusNFeBackupService(FakeClient(resource))

        selecionados = service.selecionar("14004764000160", {"202607"})
        xmls = list(service.iter_xmls(selecionados))

        self.assertEqual(selecionados, [BackupFocus("202607", url_julho)])
        self.assertEqual(len(xmls), 1)
        self.assertEqual(xmls[0].mes, "202607")
        self.assertEqual(xmls[0].nome, "nota.xml")
        self.assertIn("<nfeProc", xmls[0].conteudo)

    def test_rejeita_conteudo_que_nao_e_zip(self):
        url = "https://focus.test/202607.zip"
        resource = FakeBackupsResource(
            arquivos={url: b"nao-e-zip"},
        )
        service = FocusNFeBackupService(FakeClient(resource))

        with self.assertRaisesMessage(DomainError, "backup invalido"):
            list(service.iter_xmls([BackupFocus("202607", url)]))

    def test_meses_entre_inclui_virada_do_ano(self):
        self.assertEqual(
            meses_entre(date(2025, 12, 20), date(2026, 2, 1)),
            {"202512", "202601", "202602"},
        )
