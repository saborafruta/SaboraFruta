from decimal import Decimal
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.services.transferencia_nfe import _cfop_transferencia
from apps.fiscal.integrations.focusnfe.resources.mdfe import MDFeResource
from apps.fiscal.integrations.focusnfe.exceptions import FocusNFeProcessingError
from apps.fiscal.services.focusnfe_service import FocusNFeService
from apps.financeiro.constants.enums import StatusDocumentoFiscal, TipoDocumentoFiscal
from apps.logistica.services.mdfe_focusnfe import (
    _chave_nfe_vinculada,
    _obter_ou_criar_documento_mdfe,
    _preparar_reemissao,
    _validar_transporte,
    construir_payload_mdfe,
)
from apps.logistica.views import (
    _dados_destino_nfe,
    _peso_bruto_nfe,
    _peso_produtos_nfe,
    _rota_filiais_nfe,
)


class TransferenciaFiscalTests(SimpleTestCase):
    def test_reemissao_limpa_retorno_sem_apagar_documento(self):
        documento = SimpleNamespace(
            status="rejeitada",
            codigo_status_sefaz="539",
            mensagem_sefaz="Duplicidade",
            chave="1" * 44,
            protocolo="123",
            data_autorizacao=timezone.now(),
            data_cancelamento=None,
            save=Mock(),
        )
        mdfe = SimpleNamespace(
            documento_fiscal=documento,
            status="rejeitado",
            chave_acesso="1" * 44,
            protocolo_autorizacao="123",
            data_autorizacao=timezone.now(),
            data_cancelamento=None,
            mensagem_sefaz="Duplicidade",
            save=Mock(),
        )

        _preparar_reemissao(mdfe)

        self.assertEqual(documento.status, StatusDocumentoFiscal.PENDENTE)
        self.assertIsNone(documento.chave)
        self.assertEqual(documento.codigo_status_sefaz, "")
        self.assertEqual(mdfe.status, "rascunho")
        self.assertEqual(mdfe.mensagem_sefaz, "")
        documento.save.assert_called_once()
        mdfe.save.assert_called_once()

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
            destinatario_tipo="filial",
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

    def test_peso_produtos_informa_cadastros_incompletos(self):
        itens = Mock()
        itens.select_related.return_value = [
            SimpleNamespace(
                produto=SimpleNamespace(nome="ABACAXI", peso_bruto=Decimal("1.250")),
                quantidade=Decimal("2"),
                descricao="ABACAXI",
                codigo_produto="1",
            ),
            SimpleNamespace(
                produto=SimpleNamespace(nome="CAJA", peso_bruto=None),
                quantidade=Decimal("3"),
                descricao="CAJA",
                codigo_produto="2",
            ),
        ]
        documento = SimpleNamespace(itens=itens)

        peso, faltantes = _peso_produtos_nfe(documento)

        self.assertEqual(peso, Decimal("2.500"))
        self.assertEqual(faltantes, ["CAJA"])

    @patch("apps.logistica.views._dados_destino_nfe")
    @patch("apps.logistica.views._filial_destino_nfe")
    def test_rota_mdfe_usa_municipios_das_filiais(
        self, destino_mock, dados_destino_mock
    ):
        origem = SimpleNamespace(
            uf="RN", cidade="Macaiba", codigo_municipio_ibge="2407104",
        )
        destino_mock.return_value = SimpleNamespace(
            uf="RN", cidade="Natal", codigo_municipio_ibge="2408102",
        )
        dados_destino_mock.return_value = {
            "uf": "RN",
            "cidade": "Natal",
            "codigo_municipio": "2408102",
        }

        rota = _rota_filiais_nfe(SimpleNamespace(filial=origem))

        self.assertEqual(rota["municipio_carregamento"], "Macaiba")
        self.assertEqual(rota["codigo_municipio_carregamento"], "2407104")
        self.assertEqual(rota["municipio_descarregamento"], "Natal")
        self.assertEqual(rota["codigo_municipio_descarregamento"], "2408102")

    @patch("apps.logistica.views._filial_destino_nfe", return_value=None)
    def test_rota_mdfe_recupera_destino_do_xml(self, _destino_mock):
        origem = SimpleNamespace(
            uf="RN", cidade="Macaiba", codigo_municipio_ibge="2407104",
        )
        documento = SimpleNamespace(
            filial=origem,
            destinatario_snapshot={},
            destinatario_tipo="",
            destinatario_id=None,
            xml_assinado="""
                <NFe xmlns="http://www.portalfiscal.inf.br/nfe">
                  <infNFe><dest><enderDest>
                    <cMun>2408102</cMun><xMun>Natal</xMun><UF>RN</UF>
                  </enderDest></dest></infNFe>
                </NFe>
            """,
            xml_retorno="",
            xml_enviado="",
        )

        rota = _rota_filiais_nfe(documento)

        self.assertEqual(rota["municipio_carregamento"], "Macaiba")
        self.assertEqual(rota["municipio_descarregamento"], "Natal")
        self.assertEqual(rota["codigo_municipio_descarregamento"], "2408102")

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
        self.assertEqual(payload["veiculo_tracao"]["placa"], "ABC1D23")
        self.assertEqual(payload["veiculo_tracao"]["placa_veiculo"], "ABC1D23")
        self.assertEqual(payload["veiculo_tracao"]["tipo_rodado_veiculo"], "01")
        self.assertEqual(payload["veiculo_tracao"]["tipo_carroceria_veiculo"], "02")
        self.assertEqual(payload["modal_rodoviario"]["placa_veiculo"], "ABC1D23")
        self.assertEqual(payload["modal_rodoviario"]["tara_veiculo"], 2500)
        self.assertEqual(
            payload["modal_rodoviario"]["condutores"],
            [{"nome": "Maria Silva", "cpf": "12345678901"}],
        )
        self.assertEqual(payload["modal_rodoviario"]["tipo_rodado_veiculo"], "01")
        self.assertEqual(
            payload["modal_rodoviario"]["tipo_carroceria_veiculo"], "02"
        )
        self.assertEqual(
            payload["modal_rodoviario"]["uf_licenciamento_veiculo"], "RN"
        )
        self.assertEqual(payload["codigo_veiculo"], "ABC1D23")
        self.assertEqual(payload["placa_veiculo"], "ABC1D23")
        self.assertEqual(payload["tara_veiculo"], 2500)
        self.assertEqual(
            payload["condutores"],
            [{"nome": "Maria Silva", "cpf": "12345678901"}],
        )
        self.assertEqual(payload["tipo_rodado_veiculo"], "01")
        self.assertEqual(payload["tipo_carroceria_veiculo"], "02")
        self.assertEqual(payload["uf_licenciamento_veiculo"], "RN")
        self.assertEqual(payload["renavam_veiculo"], "12345678901")
        self.assertEqual(payload["capacidade_kg_veiculo"], 6000)
        self.assertNotIn("placa", payload)
        self.assertEqual(
            payload["municipios_descarregamento"][0]["notas_fiscais"],
            [{"chave_nfe": "2" * 44}],
        )
        self.assertEqual(payload["codigo_unidade_medida_peso_bruto"], "01")
        self.assertEqual(
            payload["data_hora_previsto_inicio_viagem"],
            "2026-07-29T22:30:00-03:00",
        )

    def test_payload_mdfe_usa_chave_salva_na_vinculacao(self):
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
            chave="",
            numero=1,
        )
        vinculo = SimpleNamespace(
            documento_fiscal=nfe,
            chave_acesso="2" * 44,
        )

        class Documentos:
            def select_related(self, *args):
                return self

            def filter(self, **kwargs):
                return [vinculo]

        mdfe = SimpleNamespace(
            filial=filial,
            documentos=Documentos(),
            serie="1",
            numero=1,
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
                "uf_placa": "RN",
                "tipo_rodado": "Truck",
                "tipo_carroceria": "Fechada",
            },
            valor_total=Decimal("52.50"),
            peso_total_kg=Decimal("5.400"),
            observacao="Transferencia entre filiais.",
            data_hora_inicio_viagem=timezone.make_aware(
                datetime(2026, 7, 30, 2, 30)
            ),
        )

        payload = construir_payload_mdfe(mdfe)

        self.assertEqual(
            payload["municipios_descarregamento"][0]["notas_fiscais"],
            [{"chave_nfe": "2" * 44}],
        )

    def test_chave_vinculada_valida_prevalece_sobre_campo_fiscal_invalido(self):
        documento = SimpleNamespace(
            chave="1",
            xml_assinado="",
            xml_retorno="",
            xml_enviado="",
        )
        vinculo = SimpleNamespace(
            documento_fiscal=documento,
            chave_acesso="NFe" + ("2" * 44),
        )

        self.assertEqual(_chave_nfe_vinculada(vinculo), "2" * 44)

    def test_chave_vinculada_e_recuperada_do_xml_autorizado(self):
        documento = SimpleNamespace(
            chave="",
            xml_assinado='<NFe><infNFe Id="NFe' + ("3" * 44) + '"></infNFe></NFe>',
            xml_retorno="",
            xml_enviado="",
        )
        vinculo = SimpleNamespace(documento_fiscal=documento, chave_acesso="")

        self.assertEqual(_chave_nfe_vinculada(vinculo), "3" * 44)

    @patch("apps.logistica.services.mdfe_focusnfe.DocumentoFiscal.objects")
    def test_documento_fiscal_do_mdfe_manual_preenche_snapshot_obrigatorio(
        self, documentos_mock
    ):
        documentos_mock.filter.return_value.first.return_value = None
        documento = Mock()
        documentos_mock.create.return_value = documento
        mdfe = SimpleNamespace(
            pk=1,
            filial=SimpleNamespace(cnpj="14004764000160"),
            documento_fiscal=None,
            numero=1,
            serie="1",
            valor_total=Decimal("52.50"),
            responsavel=None,
            save=Mock(),
        )

        resultado = _obter_ou_criar_documento_mdfe(mdfe)

        self.assertIs(resultado, documento)
        self.assertEqual(
            documentos_mock.create.call_args.kwargs["destinatario_snapshot"],
            {},
        )

    @patch("apps.logistica.services.mdfe_focusnfe.DocumentoFiscal.objects")
    def test_documento_fiscal_da_nfe_nunca_e_reutilizado_pelo_mdfe(
        self, documentos_mock
    ):
        nfe = SimpleNamespace(
            tipo_documento=TipoDocumentoFiscal.NFE,
            origem_tipo="transferencia_loja",
            origem_id=10,
        )
        documento_mdfe = Mock()
        documentos_mock.filter.return_value.first.return_value = None
        documentos_mock.create.return_value = documento_mdfe
        mdfe = SimpleNamespace(
            pk=1,
            filial=SimpleNamespace(cnpj="14004764000160"),
            documento_fiscal=nfe,
            numero=1,
            serie="2",
            valor_total=Decimal("52.50"),
            responsavel=None,
            save=Mock(),
        )

        resultado = _obter_ou_criar_documento_mdfe(mdfe)

        self.assertIs(resultado, documento_mdfe)
        self.assertIs(mdfe.documento_fiscal, documento_mdfe)
        documentos_mock.create.assert_called_once()
        self.assertEqual(
            documentos_mock.create.call_args.kwargs["tipo_documento"],
            TipoDocumentoFiscal.MDFE,
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

    def test_emissao_rejeita_resposta_nao_estruturada_sem_gerar_attribute_error(self):
        client = Mock()
        client.mdfe.endpoint = "mdfe"
        client.mdfe.autorizar.return_value = "resposta inesperada"
        documento = SimpleNamespace(
            pk=10,
            tipo_documento="mdfe",
            status=StatusDocumentoFiscal.PENDENTE,
            tentativas_envio=0,
        )
        service = FocusNFeService(client=client)

        with patch.object(service, "_registrar_log"), self.assertRaises(
            FocusNFeProcessingError
        ):
            service.emitir(documento, {"numero": 1})
