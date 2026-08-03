"""
Download de PDF fiscal (DANFE / DAMDFE).

O provider as vezes responde 200 com um JSON no lugar do arquivo -- documento
ainda em processamento, referencia inexistente, cota estourada. O cliente HTTP
devolve esses bytes como se fossem o PDF, a view os serve com
`Content-Type: application/pdf`, e o navegador mostra apenas
"Falha ao carregar documento PDF".

Isso e um beco sem saida: nao diz o que houve nem o que fazer, e o motivo real
-- que o provider mandou por escrito -- se perde no caminho. Estes testes fixam
a checagem que traz o motivo ate a tela.
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.fiscal.integrations.focusnfe.exceptions import FocusNFeError
from apps.fiscal.services.focusnfe_service import FocusNFeService

PDF_VALIDO = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF'


class ExigirPdfTests(TestCase):
    """A checagem isolada: o que passa e o que nao passa."""

    def _checar(self, conteudo):
        FocusNFeService._exigir_pdf(conteudo)

    def test_pdf_de_verdade_passa(self):
        self._checar(PDF_VALIDO)   # nao levanta

    def test_json_do_provider_vira_erro_com_o_motivo(self):
        corpo = b'{"status":"processando_autorizacao","mensagem":"aguarde"}'

        with self.assertRaises(FocusNFeError) as ctx:
            self._checar(corpo)

        # O motivo do provider precisa chegar inteiro ate quem le a mensagem.
        self.assertIn('processando_autorizacao', str(ctx.exception))

    def test_html_de_erro_tambem_e_recusado(self):
        with self.assertRaises(FocusNFeError):
            self._checar(b'<html><body>502 Bad Gateway</body></html>')

    def test_resposta_vazia_orienta_a_tentar_de_novo(self):
        """
        Vazio logo apos autorizar e o caso comum: o DAMDFE ainda nao existe do
        lado do provider. Mandar tentar de novo resolve; um erro generico nao.
        """
        with self.assertRaises(FocusNFeError) as ctx:
            self._checar(b'')
        self.assertIn('aguarde', str(ctx.exception).lower())

    def test_none_tambem_e_recusado(self):
        with self.assertRaises(FocusNFeError):
            self._checar(None)

    def test_mensagem_longa_e_truncada(self):
        """Um HTML inteiro na tela seria ilegivel."""
        with self.assertRaises(FocusNFeError) as ctx:
            self._checar(b'x' * 5000)
        self.assertLess(len(str(ctx.exception)), 400)

    def test_pdf_nao_pode_ser_confundido_por_conter_a_marca_no_meio(self):
        """A assinatura vale so no inicio do arquivo."""
        with self.assertRaises(FocusNFeError):
            self._checar(b'{"erro":"veja o %PDF anexo"}')


class BaixarPdfTests(TestCase):
    """O caminho completo do servico, com o provider dublado."""

    def _servico_com_retorno(self, retorno):
        servico = FocusNFeService(client=MagicMock())
        recurso = MagicMock()
        recurso.baixar_pdf.return_value = retorno
        p = patch.object(FocusNFeService, '_resource', return_value=recurso)
        p.start()
        self.addCleanup(p.stop)
        return servico

    def _documento(self):
        doc = MagicMock()
        doc.tipo_documento = 'mdfe'
        return doc

    def test_devolve_os_bytes_quando_e_pdf(self):
        servico = self._servico_com_retorno(PDF_VALIDO)

        with patch('apps.fiscal.services.focusnfe_service.gerar_ref',
                   return_value='ref-1'):
            self.assertEqual(servico.baixar_pdf(self._documento()), PDF_VALIDO)

    def test_nao_entrega_json_disfarcado_de_pdf(self):
        """
        Sem esta checagem, estes bytes chegavam ao navegador rotulados como
        application/pdf -- e o usuario via so "Falha ao carregar documento".
        """
        servico = self._servico_com_retorno(b'{"erro":"ref nao encontrada"}')

        with patch('apps.fiscal.services.focusnfe_service.gerar_ref',
                   return_value='ref-1'):
            with self.assertRaises(FocusNFeError) as ctx:
                servico.baixar_pdf(self._documento())

        self.assertIn('ref nao encontrada', str(ctx.exception))

    def test_tipo_sem_suporte_a_pdf_avisa(self):
        servico = FocusNFeService(client=MagicMock())
        recurso = MagicMock(spec=[])          # sem `baixar_pdf`
        p = patch.object(FocusNFeService, '_resource', return_value=recurso)
        p.start()
        self.addCleanup(p.stop)

        with self.assertRaises(ValueError):
            servico.baixar_pdf(self._documento())
