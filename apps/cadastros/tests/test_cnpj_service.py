from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from apps.cadastros.services.cnpj_service import CnpjService
from apps.core.services.exceptions import DadosInvalidosError


@override_settings(BRASILAPI_CNPJ_URL='https://brasilapi.test/{cnpj}')
class CnpjServiceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch('apps.cadastros.services.cnpj_service.requests.get')
    def test_normaliza_dados_da_brasilapi(self, get):
        response = Mock(status_code=200)
        response.json.return_value = {
            'razao_social': 'Empresa Teste LTDA',
            'nome_fantasia': 'Empresa Teste',
            'descricao_tipo_de_logradouro': 'Rua',
            'logradouro': 'Principal',
            'numero': '10',
            'bairro': 'Centro',
            'municipio': 'Natal',
            'uf': 'RN',
            'cep': '59000-000',
            'codigo_municipio': 2408102,
            'descricao_situacao_cadastral': 'ATIVA',
        }
        get.return_value = response

        resultado = CnpjService.consultar('71.345.678/0001-94')

        self.assertEqual(resultado['endereco'], 'Rua Principal')
        self.assertEqual(resultado['cep'], '59000000')
        self.assertEqual(resultado['codigo_municipio_ibge'], '2408102')
        get.assert_called_once_with('https://brasilapi.test/71345678000194', timeout=8)

    def test_rejeita_cnpj_incompleto(self):
        with self.assertRaisesMessage(DadosInvalidosError, 'CNPJ deve conter 14 dígitos.'):
            CnpjService.consultar('123')
