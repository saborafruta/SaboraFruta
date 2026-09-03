from datetime import date, datetime
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.fiscal.models import AliquotaIBPT
from apps.fiscal.services.ibpt_service import (
    consultar_ncm_ibpt, obter_aliquota_ibpt, sincronizar_tabela_ibpt,
)
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

    @patch('apps.fiscal.services.ibpt_service.requests.get')
    def test_a_consulta_por_ncm_nao_usa_o_user_agent_padrao_do_requests(self, get):
        """
        O WAF do provedor (ModSecurity) bloqueia com 406 qualquer requisicao
        cujo User-Agent comece com "python-requests/" -- o padrao que a
        biblioteca manda sozinha, tratado la como assinatura de bot.
        """
        resposta = Mock()
        resposta.json.return_value = {
            'codigo': '20089900', 'descricao': 'x', 'nacionalfederal': '1',
            'importadosfederal': '1', 'estadual': '1', 'municipal': '0',
            'vigenciainicio': '2026-01-01', 'vigenciafim': '2026-12-31',
            'versao': '26.1.L', 'fonte': 'IBPT', 'uf': 'RN',
        }
        get.return_value = resposta

        consultar_ncm_ibpt('20089900', 'RN')

        headers = get.call_args.kwargs['headers']
        self.assertNotIn('python-requests', headers.get('User-Agent', ''))

    @patch('apps.fiscal.services.ibpt_service.requests.get')
    def test_a_sincronizacao_da_tabela_tambem_nao_usa_o_user_agent_padrao(self, get):
        resposta = Mock()
        resposta.json.return_value = {
            'uf': 'RN', 'versao': '26.1.L',
            'ncm': [{
                'codigo': '20089900', 'tipo': 0, 'descricao': 'x', 'nacionalfederal': '1',
                'importadosfederal': '1', 'estadual': '1', 'municipal': '0',
                'vigenciainicio': '2026-01-01', 'vigenciafim': '2026-12-31',
                'versao': '26.1.L', 'fonte': 'IBPT',
            }] * 10000,
        }
        get.return_value = resposta

        sincronizar_tabela_ibpt('RN')

        headers = get.call_args.kwargs['headers']
        self.assertNotIn('python-requests', headers.get('User-Agent', ''))

    @patch('apps.fiscal.services.ibpt_service.requests.get')
    def test_a_sincronizacao_ignora_codigo_de_servico_misturado_na_lista(self, get):
        """
        O payload do IBPT mistura NCM de mercadoria (tipo 0, 8 digitos) com
        NBS de servico (tipo 1, 9 digitos) e codigo LC 116 (tipo 2, 4
        digitos) na mesma lista. Um item de servico nao e' um "NCM
        invalido" que deveria derrubar a sincronizacao inteira -- e' um
        tipo de linha que esta tabela nao usa.
        """
        def mercadoria(codigo):
            return {
                'codigo': codigo, 'tipo': 0, 'descricao': 'Preparacoes de frutas',
                'nacionalfederal': '13.45', 'importadosfederal': '21.46',
                'estadual': '20.00', 'municipal': '0.00',
                'vigenciainicio': '2026-01-01', 'vigenciafim': '2026-12-31',
                'versao': '26.2.A', 'fonte': 'IBPT',
            }
        servico_nbs = {**mercadoria('20089900'), 'codigo': '101011000', 'tipo': 1}
        servico_lc116 = {**mercadoria('20089900'), 'codigo': '0101', 'tipo': 2}
        mercadorias = [mercadoria(f'{20080000 + i:08d}') for i in range(10000)]

        resposta = Mock()
        resposta.json.return_value = {
            'uf': 'RN', 'versao': '26.2.A',
            'ncm': [servico_nbs, servico_lc116] + mercadorias,
        }
        get.return_value = resposta

        resultado = sincronizar_tabela_ibpt('RN')

        self.assertEqual(resultado['quantidade'], 10000)
        self.assertFalse(AliquotaIBPT.objects.filter(ncm='101011000').exists())
        self.assertFalse(AliquotaIBPT.objects.filter(ncm='0101').exists())
        self.assertTrue(AliquotaIBPT.objects.filter(ncm='20080000').exists())
