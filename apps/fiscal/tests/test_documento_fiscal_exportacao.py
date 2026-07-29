from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.fiscal.views import _pasta_origem_documento, _situacao_xml_backup


class DocumentoFiscalExportacaoTests(SimpleTestCase):
    def _documento(self, origem_tipo="", tipo_documento="nfe"):
        return SimpleNamespace(
            origem_tipo=origem_tipo,
            tipo_documento=tipo_documento,
        )

    def test_separa_origens_operacionais(self):
        cenarios = [
            ("venda_pdv", "nfe", "Vendas"),
            ("transferencia_estoque", "nfe", "Transferencias"),
            ("devolucao_fornecedor", "nfe", "Devolucoes/Fornecedores"),
            ("devolucao_fabricante", "nfe", "Devolucoes/Fabricantes"),
            ("devolucao_cliente", "nfe", "Devolucoes/Clientes"),
            ("cte", "cte", "Logistica"),
            ("", "nfse", "Servicos"),
            ("", "nfe", "Saidas"),
        ]
        for origem_tipo, tipo_documento, esperado in cenarios:
            with self.subTest(origem_tipo=origem_tipo):
                self.assertEqual(
                    _pasta_origem_documento(
                        self._documento(origem_tipo, tipo_documento)
                    ),
                    esperado,
                )

    def test_separa_cancelamentos_e_eventos_do_backup(self):
        self.assertEqual(
            _situacao_xml_backup(
                "<procEventoNFe><evento><tpEvento>110111</tpEvento></evento></procEventoNFe>"
            ),
            "canceladas",
        )
        self.assertEqual(
            _situacao_xml_backup("<procEventoNFe><evento /></procEventoNFe>"),
            "eventos",
        )
        self.assertEqual(
            _situacao_xml_backup("<nfeProc />"),
            "emitidas",
        )
