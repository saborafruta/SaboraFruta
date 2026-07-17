from datetime import date, datetime
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.fiscal.services.ibpt_service import obter_aliquota_ibpt
from apps.fiscal.services.ibpt_scheduler import proxima_execucao


class IBPTServiceTests(TestCase):
    def test_agendador_programa_execucao_para_0310(self):
        agora = timezone.make_aware(datetime(2026, 7, 17, 4, 0))

        proxima = proxima_execucao(agora)

        self.assertEqual(proxima.date(), date(2026, 7, 18))
        self.assertEqual((proxima.hour, proxima.minute), (3, 10))

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
