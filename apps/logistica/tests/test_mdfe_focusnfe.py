from decimal import Decimal
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.services.transferencia_nfe import _cfop_transferencia
from apps.fiscal.integrations.focusnfe.resources.mdfe import MDFeResource
from apps.financeiro.constants.enums import StatusDocumentoFiscal
from apps.logistica.services.mdfe_focusnfe import (
    _validar_transporte,
    construir_payload_mdfe,
)
from apps.logistica.views import _dados_destino_nfe, _peso_bruto_nfe


class TransferenciaFiscalTests(SimpleTestCase):
    @patch("apps.logistica.views.Filial.objects.filter")
    def test_destino_prioriza_endereco_atual_da_filial(self, filter_mock):
        filial_destino = SimpleNamespace(
            razao_social="SABORAFRUTA INDUSTRIA ALIMENTICIA LTDA",
            endereco="Avenida Capitao Mor Gouveia",
            numero="3005",
            complemento="BOX 11",
            bairro="Lagoa Nova",
            cidade="Natal",
            uf="RN",
            cep="59063410",
            codigo_municipio_ibge="2408102",
        )
        filter_mock.return_value.first.return_value = filial_destino
        documento = SimpleNamespace(
            destinatario_snapshot={
                "nome": "SABORAFRUTA",
                "logradouro": "Endereco antigo",
                "cidade": "Cidade antiga",
            },
            destinatario_id=2,
            xml_assinado="",
            xml_retorno="",
            xml_enviado="",
        )

        destino = _dados_destino_nfe(documento)

        filter_mock.assert_called_once_with(pk=2)
        self.assertEqual(destino["cidade"], "Natal")
        self.assertEqual(destino["codigo_municipio"], "2408102")
        self.assertIn("Avenida Capitao Mor Gouveia 3005", destino["endereco_completo"])

    def test_destino_e_recuperado_do_xml_quando_snapshot_antigo_esta_incompleto(self):
        documento = SimpleNamespace(
            destinatario_snapshot={"nome": "SABORAFRUTA"},
            destinatario_tipo="",
            destinatario_id=None,
            xml_assinado="""
                <NFe xmlns="http://www.portalfiscal.inf.br/nfe">
                  <infNFe>
                    <dest><enderDest>
                      <xLgr>Avenida Capitao Mor Gouveia</xLgr>
                      <nro>3005</nro>
                      <xBairro>Lagoa Nova</xBairro>
                      <cMun>2408102</cMun>
                      <xMun>Natal</xMun>
                      <UF>RN</UF>
                      <CEP>59063410</CEP>
                    </enderDest></dest>
                  </infNFe>
                </NFe>
            """,
            xml_retorno="",
            xml_enviado="",
        )

        destino = _dados_destino_nfe(documento)

        self.assertEqual(destino["cidade"], "Natal")
        self.assertEqual(destino["codigo_municipio"], "2408102")
        self.assertIn("Avenida Capitao Mor Gouveia 3005", destino["endereco_completo"])

    def test_peso_bruto_e_reaproveitado_do_xml_da_nfe(self):
        documento = SimpleNamespace(
            xml_assinado="""
                <NFe xmlns="http://www.portalfiscal.inf.br/nfe">
                  <infNFe>
                    <transp>
                      <vol><pesoB>125.500</pesoB></vol>
                      <vol><pesoB>24.500</pesoB></vol>
                    </transp>
                  </infNFe>
                </NFe>
            """,
            xml_retorno="",
            xml_enviado="",
        )

        self.assertEqual(_peso_bruto_nfe(documento), Decimal("150.000"))

    def test_cfop_producao_propria(self):
        self.assertEqual(_cfop_transferencia("RN", "RN", "producao_propria"), "5151")
        self.assertEqual(_cfop_transferencia("RN", "PB", "producao_propria"), "6151")

    def test_cfop_mercadoria_de_terceiros(self):
        self.assertEqual(_cfop_transferencia("RN", "RN", "terceiros"), "5152")
        self.assertEqual(_cfop_transferencia("RN", "PB", "terceiros"), "6152")

    def test_origem_desconhecida_nao_emite(self):
        with self.assertRaises(DadosInvalidosError):
            _cfop_transferencia("RN", "RN", "indefinida")

    def test_transporte_exige_dados_fiscais(self):
        motorista = SimpleNamespace(cpf="")
        veiculo = SimpleNamespace(
            placa="", tara=Decimal("0"), uf_placa="", tipo_carroceria="",
        )
        with self.assertRaisesMessage(DadosInvalidosError, "CPF do motorista"):
            _validar_transporte(motorista, veiculo, Decimal("0"))

    def test_payload_mdfe_carga_propria_vincula_nfe(self):
        filial = SimpleNamespace(
            cnpj="14004764000240",
            razao_social="SABORAFRUTA INDUSTRIA ALIMENTICIA LTDA",
            nome_fantasia="SABOR A FRUTA",
            inscricao_estadual="207184704",
            endereco="Avenida Capitao Mor Gouveia",
            numero="3005",
            complemento="BOX 11",
            bairro="Lagoa Nova",
            codigo_municipio_ibge="2408102",
            cidade="Natal",
            uf="RN",
            cep="59063410",
        )
        nfe = SimpleNamespace(
            status=StatusDocumentoFiscal.AUTORIZADA,
            chave="2" * 44,
            numero=10,
        )
        vinculo = SimpleNamespace(documento_fiscal=nfe, chave_acesso="")

        class Documentos:
            def select_related(self, *args):
                return self

            def filter(self, **kwargs):
                return [vinculo]

        mdfe = SimpleNamespace(
            filial=filial,
            documentos=Documentos(),
            serie="1",
            numero=5,
            uf_carregamento="RN",
            uf_descarregamento="RN",
            codigo_municipio_carregamento="2408102",
            municipio_carregamento="Natal",
            codigo_municipio_descarregamento="2408102",
            municipio_descarregamento="Natal",
            veiculo_placa="ABC1D23",
            motorista_nome="Maria Silva",
            motorista_cpf="12345678901",
            transporte_metadados={
                "tara": "2500",
                "capacidade_kg": "6000",
                "renavam": "12345678901",
                "uf_placa": "RN",
                "tipo_rodado": "Truck",
                "tipo_carroceria": "Fechada",
            },
            valor_total=Decimal("1200.00"),
            peso_total_kg=Decimal("850.000"),
            observacao="Transferencia entre filiais.",
            data_hora_inicio_viagem=timezone.make_aware(
                datetime(2026, 7, 29, 22, 30)
            ),
        )

        payload = construir_payload_mdfe(mdfe)

        self.assertEqual(payload["emitente"], "2")
        self.assertEqual(payload["seguros_carga"], [{"responsavel_seguro": "1"}])
        self.assertEqual(payload["veiculo_tracao"]["tipo_rodado_veiculo"], "01")
        self.assertEqual(payload["veiculo_tracao"]["tipo_carroceria_veiculo"], "02")
        self.assertEqual(
            payload["municipios_descarregamento"][0]["notas_fiscais"],
            [{"chave_nfe": "2" * 44}],
        )
        self.assertEqual(payload["codigo_unidade_medida_peso_bruto"], "01")
        self.assertEqual(
            payload["data_hora_previsto_inicio_viagem"],
            "2026-07-29T22:30:00-03:00",
        )


class MDFeResourceTests(SimpleTestCase):
    def setUp(self):
        self.http = Mock()
        self.resource = MDFeResource(self.http)

    def test_encerramento_usa_campos_oficiais_focus(self):
        self.http.post.return_value = {"status": "encerrado"}

        self.resource.encerrar(
            "df-10",
            data="2026-07-27",
            sigla_uf="RN",
            nome_municipio="Natal",
        )

        self.http.post.assert_called_once_with(
            "/v2/mdfe/df-10/encerrar",
            json_body={
                "nome_municipio": "Natal",
                "sigla_uf": "RN",
                "data": "2026-07-27",
            },
        )

    def test_inclusoes_usam_endpoints_v2_atuais(self):
        self.resource.incluir_condutor("df-10", "Maria Silva", "12345678901")
        self.http.post.assert_called_with(
            "/v2/mdfe/df-10/inclusao_condutor",
            json_body={"nome": "Maria Silva", "cpf": "12345678901"},
        )

        self.resource.incluir_dfe("df-10", {"chave_nfe": "1" * 44})
        self.http.post.assert_called_with(
            "/v2/mdfe/df-10/inclusao_dfe",
            json_body={"chave_nfe": "1" * 44},
        )
