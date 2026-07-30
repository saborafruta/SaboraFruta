from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.services.transferencia_nfe import (
    _validar_cancelamento_sem_mdfe_ativo,
    construir_payload_transferencia,
)
from apps.logistica.models import MDFe


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
            "icms_situacao_tributaria": "102",
            "ibs_cbs_situacao_tributaria": "000",
            "ibs_cbs_classificacao_tributaria": "000001",
            "ibs_cbs_base_calculo": 10,
            "ibs_uf_aliquota": 0.1,
            "cbs_aliquota": 0.9,
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
        self.assertEqual(payload["modalidade_frete"], "3")
        item = payload["items"][0]
        self.assertEqual(item["cfop"], "5151")
        self.assertEqual(item["icms_situacao_tributaria"], "400")
        self.assertEqual(item["pis_situacao_tributaria"], "08")
        self.assertEqual(item["cofins_situacao_tributaria"], "08")
        self.assertEqual(item["ibs_cbs_situacao_tributaria"], "410")
        self.assertEqual(item["ibs_cbs_classificacao_tributaria"], "410002")
        self.assertNotIn("ibs_cbs_base_calculo", item)
        self.assertNotIn("ibs_uf_aliquota", item)
        self.assertNotIn("cbs_aliquota", item)


class TransferenciaNFeCancelamentoTests(SimpleTestCase):
    @staticmethod
    def _mock_vinculo(manager, status):
        mdfe = SimpleNamespace(
            numero=7,
            serie="1",
            status=status,
            get_status_display=lambda: dict(MDFe.Status.choices)[status],
        )
        vinculo = SimpleNamespace(mdfe=mdfe)
        (
            manager.select_related.return_value.filter.return_value.exclude.return_value
            .order_by.return_value.first.return_value
        ) = vinculo

    @patch("apps.logistica.models.DocumentoMDFe.objects")
    def test_bloqueia_cancelamento_quando_mdfe_esta_autorizado(self, manager):
        self._mock_vinculo(manager, MDFe.Status.AUTORIZADO)

        with self.assertRaisesMessage(
            DadosInvalidosError,
            "Cancele o MDF-e antes da NF-e",
        ):
            _validar_cancelamento_sem_mdfe_ativo(SimpleNamespace(pk=10))

    @patch("apps.logistica.models.DocumentoMDFe.objects")
    def test_permite_cancelamento_sem_mdfe_ativo(self, manager):
        (
            manager.select_related.return_value.filter.return_value.exclude.return_value
            .order_by.return_value.first.return_value
        ) = None

        _validar_cancelamento_sem_mdfe_ativo(SimpleNamespace(pk=10))
