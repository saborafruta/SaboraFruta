from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.estoque.services.transferencia_nfe import construir_payload_transferencia


class TransferenciaNFePayloadTests(SimpleTestCase):
    @patch("apps.estoque.services.transferencia_nfe._validar_regras_mei")
    @patch("apps.estoque.services.transferencia_nfe._montar_item_fiscal")
    def test_payload_inclui_identificacao_e_endereco_do_destinatario(
        self,
        montar_item,
        _validar_regras_mei,
    ):
        montar_item.return_value = {
            "valor_bruto": 10,
            "valor_outras_despesas": 0,
            "icms_valor_st": 0,
            "ipi_valor": 0,
        }
        origem = SimpleNamespace(cnpj="14004764000160", uf="RN")
        destino = SimpleNamespace(
            cnpj="14004764000240",
            razao_social="SABORAFRUTA INDUSTRIA ALIMENTICIA LTDA",
            inscricao_estadual="207184704",
            endereco="Avenida Capitao Mor Gouveia",
            numero="3005",
            complemento="BOX 11",
            bairro="Lagoa Nova",
            cidade="Natal",
            uf="RN",
            cep="59063410",
            codigo_municipio_ibge="2408102",
        )

        payload = construir_payload_transferencia(
            origem,
            destino,
            [SimpleNamespace()],
            numero_nfe=1,
            serie_nfe=1,
        )

        self.assertEqual(payload["cnpj_destinatario"], "14004764000240")
        self.assertEqual(
            payload["nome_destinatario"],
            "SABORAFRUTA INDUSTRIA ALIMENTICIA LTDA",
        )
        self.assertEqual(payload["inscricao_estadual_destinatario"], "207184704")
        self.assertEqual(payload["logradouro_destinatario"], "Avenida Capitao Mor Gouveia")
        self.assertEqual(payload["codigo_municipio_destinatario"], "2408102")
