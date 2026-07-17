from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings

from apps.fiscal.services.ibpt_service import obter_aliquota_ibpt


class IBPTServiceTests(TestCase):
    @override_settings(IBPT_AUTO_SYNC=True)
    @patch('apps.fiscal.services.ibpt_service.requests.get')
    def test_consulta_e_armazena_aliquota_vigente(self, get):
        resposta = Mock()
        resposta.json.return_value = {
            'codigo': '20089900',
            'descricao': 'Preparacoes de frutas',
            'nacionalfederal': '13.45',
            'importadosfederal': '21.46',
            'estadual': '20.00',
            'municipal': '0.00',
            'vigenciainicio': '2026-06-20',
            'vigenciafim': '2026-07-31',
            'versao': '26.1.L',
            'fonte': 'IBPT/empresometro.com.br',
            'uf': 'RN',
        }
        get.return_value = resposta

        aliquota = obter_aliquota_ibpt('20089900', 'RN', date(2026, 7, 17))

        self.assertEqual(aliquota.versao, '26.1.L')
        self.assertEqual(aliquota.federal_nacional, Decimal('13.45'))
        self.assertEqual(aliquota.estadual, Decimal('20'))
        resposta.raise_for_status.assert_called_once()
