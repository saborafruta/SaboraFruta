from django.test import SimpleTestCase

from apps.cadastros.services.cliente_import_service import ClienteImportService


class ClienteImportServiceTests(SimpleTestCase):
    def test_normaliza_cabecalhos_comuns_de_planilha(self):
        casos = {
            "Tipo Pessoa": "tipo_pessoa",
            "Razão Social": "razao_social",
            "CPF/CNPJ": "cpf_cnpj",
            "Endereço": "endereco",
            "Logradouro": "endereco",
            "Número": "numero",
            "Município": "cidade",
            "Estado": "uf",
        }

        for cabecalho, esperado in casos.items():
            with self.subTest(cabecalho=cabecalho):
                self.assertEqual(
                    ClienteImportService._normalizar_cabecalho(cabecalho),
                    esperado,
                )
