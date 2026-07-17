from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.constants.choices import TipoPessoa
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.financeiro.constants.enums import StatusDocumentoFiscal, TipoDocumentoFiscal, TipoFormaPagamento
from apps.financeiro.models import FormaPagamento
from apps.financeiro.models.fiscal import DocumentoFiscal
from apps.fiscal.services.focusnfe_service import FocusNFeService
from apps.fiscal.models import AliquotaIBPT
from apps.pdv.models import ItemVendaPDV, PagamentoVendaPDV, VendaPDV
from apps.pdv.services.nfce_payload_builder import (
    NfcePayloadBuilder,
    NfePayloadBuilder,
    _validar_configuracao_nfce,
)
from apps.pdv.services.cancelamento_fiscal_service import cancelar_venda_e_documento
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class NfePayloadBuilderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Empresa NFe LTDA",
            nome_fantasia="Empresa NFe",
            cnpj="72345678000191",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social="Filial NFe",
            nome_fantasia="Matriz",
            cnpj="72345678000192",
            uf="RN",
            codigo_regime_tributario=1,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome="Operador NFe",
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email="nfe-pdv@inoovated.com",
            nome="Usuario NFe",
            password="teste1234",
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa,
            sigla="UN",
            descricao="Unidade",
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.forma = FormaPagamento.objects.create(
            empresa=cls.empresa,
            filial=cls.filial,
            descricao="Dinheiro",
            tipo=TipoFormaPagamento.DINHEIRO,
        )

    def criar_produto(self):
        produto = Produto.objects.create(
            filial=self.filial,
            unidade_medida=self.unidade,
            descricao="Coco Ralado 250g",
            codigo="51",
            ncm="07141000",
            cfop_venda_interna="5102",
            cst_csosn="102",
            cst_pis="07",
            cst_cofins="07",
            controla_lote=False,
            preco_venda=Decimal("4.00"),
            preco_custo=Decimal("2.00"),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        return produto

    def criar_venda(self, cliente):
        produto = self.criar_produto()
        venda = VendaPDV.objects.create(
            filial=self.filial,
            numero_venda=1,
            cliente=cliente,
            status="finalizada",
            valor_subtotal=Decimal("4.00"),
            valor_total=Decimal("4.00"),
            valor_pago=Decimal("4.00"),
            usuario=self.usuario,
            data_venda=timezone.now(),
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda,
            produto=produto,
            numero_item=1,
            quantidade=Decimal("1.000"),
            unidade_medida="UN",
            valor_unitario=Decimal("4.0000"),
            valor_total=Decimal("4.00"),
        )
        PagamentoVendaPDV.objects.create(
            venda_pdv=venda,
            forma_pagamento=self.forma,
            valor=Decimal("4.00"),
        )
        return venda

    def criar_cliente(self, **kwargs):
        dados = {
            "filial": self.filial,
            "tipo_pessoa": TipoPessoa.FISICA,
            "razao_social": "THIAGO BATISTA",
            "cpf_cnpj": "04651988482",
        }
        dados.update(kwargs)
        return Cliente.objects.create(**dados)

    def test_nfe_exige_endereco_completo_do_destinatario(self):
        venda = self.criar_venda(self.criar_cliente())

        with self.assertRaisesMessage(DadosInvalidosError, "endereco completo"):
            NfePayloadBuilder.build(venda, numero_nfe=6, serie_nfe=1)

    def test_nfe_payload_leva_endereco_destinatario_e_icms_focus(self):
        cliente = self.criar_cliente(
            endereco="Rua Sao Januario",
            numero="99",
            bairro="Crespo",
            cidade="Manaus",
            uf="AM",
            cep="69073178",
            codigo_municipio_ibge="1302603",
            celular="84999998888",
            email_nfe="cliente@example.com",
        )
        venda = self.criar_venda(cliente)

        payload = NfePayloadBuilder.build(venda, numero_nfe=6, serie_nfe=1)
        item = payload["items"][0]

        self.assertEqual(payload["cpf_destinatario"], "04651988482")
        self.assertEqual(payload["logradouro_destinatario"], "Rua Sao Januario")
        self.assertEqual(payload["numero_destinatario"], "99")
        self.assertEqual(payload["bairro_destinatario"], "Crespo")
        self.assertEqual(payload["municipio_destinatario"], "Manaus")
        self.assertEqual(payload["uf_destinatario"], "AM")
        self.assertEqual(payload["cep_destinatario"], "69073178")
        self.assertEqual(payload["codigo_municipio_destinatario"], "1302603")
        self.assertEqual(payload["indicador_inscricao_estadual_destinatario"], "9")
        self.assertEqual(payload["telefone_destinatario"], "84999998888")
        self.assertEqual(payload["email_destinatario"], "cliente@example.com")
        self.assertEqual(item["icms_situacao_tributaria"], "102")
        self.assertNotIn("icms_csosn", item)
        self.assertEqual(payload["local_destino"], "2")
        self.assertEqual(item["cfop"], "6102")
        self.assertEqual(payload["consumidor_final"], 1)
        self.assertNotIn("valor_total_tributos", payload)
        self.assertNotIn("valor_total_tributos", item)

    def test_nfce_informa_troco_e_delivery(self):
        venda = self.criar_venda(None)
        venda.delivery = True
        venda.save(update_fields=["delivery"])
        pagamento = venda.pagamentos.get()
        pagamento.valor = Decimal("5.00")
        pagamento.troco = Decimal("1.00")
        pagamento.save(update_fields=["valor", "troco"])

        payload = NfcePayloadBuilder.build(venda, numero=1, serie=1)

        self.assertEqual(payload["presenca_comprador"], "4")
        self.assertEqual(payload["valor_troco"], 1.0)
        self.assertEqual(payload["formas_pagamento"][0]["valor_pagamento"], 5.0)

    def test_nfce_detalha_cartao_nao_integrado(self):
        venda = self.criar_venda(None)
        pagamento = venda.pagamentos.get()
        forma_cartao = FormaPagamento.objects.create(
            empresa=self.empresa,
            filial=self.filial,
            descricao="Credito",
            tipo=TipoFormaPagamento.CARTAO_CREDITO,
            codigo_sefaz="03",
        )
        pagamento.forma_pagamento = forma_cartao
        pagamento.autorizacao = "ABC123"
        pagamento.bandeira = "Visa"
        pagamento.save(update_fields=["forma_pagamento", "autorizacao", "bandeira"])

        forma = NfcePayloadBuilder.build(venda, numero=1, serie=1)["formas_pagamento"][0]

        self.assertEqual(forma["tipo_integracao"], "2")
        self.assertEqual(forma["numero_autorizacao"], "ABC123")
        self.assertEqual(forma["bandeira_operadora"], "01")

    def test_nfe_cnpj_nao_consumidor_final_respeita_cadastro(self):
        cliente = self.criar_cliente(
            tipo_pessoa=TipoPessoa.JURIDICA,
            razao_social="REVENDA LTDA",
            cpf_cnpj="52819813000101",
            consumidor_final=False,
            contribuinte_icms=True,
            inscricao_estadual="206474385",
            endereco="Rua das Perdizes",
            numero="50",
            bairro="Pitimbu",
            cidade="Natal",
            uf="RN",
            cep="59067480",
            codigo_municipio_ibge="2408102",
        )
        venda = self.criar_venda(cliente)

        payload = NfePayloadBuilder.build(venda, numero_nfe=7, serie_nfe=1)

        self.assertEqual(payload["consumidor_final"], 0)
        self.assertEqual(payload["indicador_inscricao_estadual_destinatario"], "1")

    def test_nfe_cpf_e_sempre_consumidor_final(self):
        cliente = self.criar_cliente(
            consumidor_final=False,
            endereco="Rua Teste",
            numero="10",
            bairro="Centro",
            cidade="Natal",
            uf="RN",
            cep="59000000",
            codigo_municipio_ibge="2408102",
        )
        venda = self.criar_venda(cliente)

        payload = NfePayloadBuilder.build(venda, numero_nfe=8, serie_nfe=1)

        self.assertEqual(payload["consumidor_final"], 1)

    def test_mei_permite_nfe_venda_com_csosn_102(self):
        cliente = self.criar_cliente(
            endereco="Rua Teste",
            numero="10",
            bairro="Centro",
            cidade="Natal",
            uf="RN",
            cep="59000000",
            codigo_municipio_ibge="2408102",
        )
        venda = self.criar_venda(cliente)
        self.filial.codigo_regime_tributario = 4
        self.filial.save(update_fields=["codigo_regime_tributario"])

        payload = NfePayloadBuilder.build(venda, numero_nfe=9, serie_nfe=1)

        self.assertEqual(payload["items"][0]["icms_situacao_tributaria"], "102")

    def test_mei_bloqueia_csosn_invalido_antes_do_envio(self):
        venda = self.criar_venda(self.criar_cliente())
        self.filial.codigo_regime_tributario = 4
        self.filial.save(update_fields=["codigo_regime_tributario"])
        produto = venda.itens.get().produto
        produto.cst_csosn = "400"
        produto.save(update_fields=["cst_csosn"])

        with self.assertRaisesMessage(DadosInvalidosError, "aceita CSOSN 102, 300"):
            NfcePayloadBuilder.build(venda, numero=1, serie=1)

    def test_nfce_sem_tabela_local_nao_inventa_vtottrib(self):
        venda = self.criar_venda(self.criar_cliente())

        payload = NfcePayloadBuilder.build(venda, numero=1, serie=1)

        self.assertEqual(payload["consumidor_final"], 1)
        self.assertNotIn("valor_total_tributos", payload)
        self.assertNotIn("valor_total_tributos", payload["items"][0])

    def test_nfce_envia_vtottrib_calculado_pela_tabela_ibpt(self):
        hoje = timezone.localdate()
        AliquotaIBPT.objects.create(
            ncm='07141000',
            uf='RN',
            descricao='Produto nacional de teste',
            federal_nacional=Decimal('13.45'),
            federal_importado=Decimal('21.46'),
            estadual=Decimal('20.00'),
            municipal=Decimal('0.00'),
            fonte='IBPT/empresometro.com.br',
            versao='26.1.L',
            vigencia_inicio=hoje - timedelta(days=10),
            vigencia_fim=hoje + timedelta(days=10),
        )
        venda = self.criar_venda(self.criar_cliente())

        payload = NfcePayloadBuilder.build(venda, numero=1, serie=1)

        self.assertEqual(payload['items'][0]['valor_total_tributos'], 1.34)
        self.assertEqual(payload['valor_total_tributos'], 1.34)
        self.assertIn('IBPT/empresometro.com.br 26.1.L', payload['informacoes_adicionais_contribuinte'])

    def test_pis_cofins_em_branco_geram_grupos_nao_tributados_completos(self):
        venda = self.criar_venda(self.criar_cliente())
        produto = venda.itens.get().produto
        produto.cst_pis = ""
        produto.cst_cofins = ""
        produto.save(update_fields=["cst_pis", "cst_cofins"])

        item = NfcePayloadBuilder.build(venda, numero=1, serie=1)["items"][0]

        self.assertEqual(item["pis_situacao_tributaria"], "07")
        self.assertEqual(item["pis_base_calculo"], 0.0)
        self.assertEqual(item["pis_valor"], 0.0)
        self.assertEqual(item["cofins_situacao_tributaria"], "07")
        self.assertEqual(item["cofins_base_calculo"], 0.0)
        self.assertEqual(item["cofins_valor"], 0.0)

    def test_calcula_icms_pis_cofins_e_ipi_com_decimal(self):
        cliente = self.criar_cliente()
        venda = self.criar_venda(cliente)
        produto = venda.itens.get().produto
        self.filial.codigo_regime_tributario = 3
        self.filial.save(update_fields=["codigo_regime_tributario"])
        produto.cst_csosn = "00"
        produto.aliquota_icms = Decimal("18.0000")
        produto.cst_pis = "01"
        produto.aliquota_pis = Decimal("1.6500")
        produto.cst_cofins = "01"
        produto.aliquota_cofins = Decimal("7.6000")
        produto.cst_ipi = "50"
        produto.aliquota_ipi = Decimal("5.00")
        produto.codigo_enquadramento_ipi = "999"
        produto.save()
        item_venda = venda.itens.get()
        item_venda.quantidade = Decimal("1")
        item_venda.valor_unitario = Decimal("100")
        item_venda.valor_total = Decimal("100")
        item_venda.save()
        venda.valor_subtotal = Decimal("100")
        venda.valor_total = Decimal("105")
        venda.save(update_fields=["valor_subtotal", "valor_total"])

        payload = NfcePayloadBuilder.build(venda, numero=1, serie=1)
        item = payload["items"][0]

        self.assertEqual(item["icms_base_calculo"], 100.0)
        self.assertEqual(item["icms_valor"], 18.0)
        self.assertEqual(item["pis_aliquota_porcentual"], 1.65)
        self.assertEqual(item["pis_valor"], 1.65)
        self.assertEqual(item["cofins_aliquota_porcentual"], 7.6)
        self.assertEqual(item["cofins_valor"], 7.6)
        self.assertEqual(item["ipi_valor"], 5.0)
        self.assertEqual(payload["valor_total"], 105.0)

    def test_rateia_desconto_e_acrescimo_na_base_dos_impostos(self):
        venda = self.criar_venda(self.criar_cliente())
        item_venda = venda.itens.get()
        item_venda.valor_unitario = Decimal("100")
        item_venda.valor_total = Decimal("100")
        item_venda.save()
        venda.valor_subtotal = Decimal("100")
        venda.valor_desconto = Decimal("10")
        venda.valor_acrescimo = Decimal("5")
        venda.valor_total = Decimal("95")
        venda.save()

        payload = NfcePayloadBuilder.build(venda, numero=1, serie=1)
        item = payload["items"][0]

        self.assertEqual(item["valor_desconto"], 10.0)
        self.assertEqual(item["valor_outras_despesas"], 5.0)
        self.assertEqual(item["valor_total"], 95.0)
        self.assertEqual(payload["valor_outras_despesas"], 5.0)
        self.assertEqual(payload["valor_total"], 95.0)

    def test_calcula_icms_st_e_fcp(self):
        venda = self.criar_venda(self.criar_cliente())
        produto = venda.itens.get().produto
        self.filial.codigo_regime_tributario = 3
        self.filial.save(update_fields=["codigo_regime_tributario"])
        produto.cst_csosn = "10"
        produto.aliquota_icms = Decimal("18")
        produto.aliquota_fcp = Decimal("2")
        produto.mva_icms_st = Decimal("40")
        produto.aliquota_icms_st = Decimal("18")
        produto.aliquota_fcp_st = Decimal("2")
        produto.save()
        item_venda = venda.itens.get()
        item_venda.valor_unitario = Decimal("100")
        item_venda.valor_total = Decimal("100")
        item_venda.save()
        venda.valor_subtotal = Decimal("100")
        venda.valor_total = Decimal("107.20")
        venda.save(update_fields=["valor_subtotal", "valor_total"])

        item = NfcePayloadBuilder.build(venda, numero=1, serie=1)["items"][0]

        self.assertEqual(item["icms_valor"], 18.0)
        self.assertEqual(item["fcp_valor"], 2.0)
        self.assertEqual(item["icms_base_calculo_st"], 140.0)
        self.assertEqual(item["icms_valor_st"], 7.2)
        self.assertEqual(item["fcp_valor_st"], 2.8)

    def test_monta_ibs_cbs_is_e_total_da_reforma(self):
        venda = self.criar_venda(self.criar_cliente())
        produto = venda.itens.get().produto
        produto.cst_ibs = produto.cst_cbs = "000"
        produto.classificacao_tributaria_ibs = produto.classificacao_tributaria_cbs = "000001"
        produto.aliquota_ibs_uf = Decimal("0.1000")
        produto.aliquota_ibs_municipal = Decimal("0")
        produto.aliquota_cbs = Decimal("0.9000")
        produto.cst_is = "000"
        produto.classificacao_tributaria_is = "000001"
        produto.aliquota_is = Decimal("1")
        produto.save()

        payload = NfcePayloadBuilder.build(venda, numero=1, serie=1)
        item = payload["items"][0]

        self.assertEqual(item["ibs_uf_valor"], 0.0)
        self.assertEqual(item["cbs_valor"], 0.04)
        self.assertEqual(item["is_aliquota"], 1.0)
        self.assertEqual(item["is_valor"], 0.04)
        self.assertEqual(item["valor_total_item"], 4.0)
        self.assertEqual(payload["ibs_cbs_is_valor_total"], 4.0)

    def test_omite_grupo_is_quando_aliquota_for_zero(self):
        venda = self.criar_venda(self.criar_cliente())
        produto = venda.itens.get().produto
        produto.cst_ibs = produto.cst_cbs = "000"
        produto.classificacao_tributaria_ibs = produto.classificacao_tributaria_cbs = "000001"
        produto.cst_is = "000"
        produto.classificacao_tributaria_is = "000001"
        produto.aliquota_is = Decimal("0")
        produto.save()

        item = NfcePayloadBuilder.build(venda, numero=1, serie=1)["items"][0]

        self.assertNotIn("is_situacao_tributaria", item)
        self.assertNotIn("is_classificacao_tributaria", item)
        self.assertNotIn("is_base_calculo", item)
        self.assertNotIn("is_aliquota", item)
        self.assertNotIn("is_valor", item)

    def test_aplica_aliquotas_de_transicao_ibs_cbs_em_2026(self):
        venda = self.criar_venda(self.criar_cliente())
        venda.data_venda = datetime(2026, 7, 17, 10, tzinfo=timezone.get_current_timezone())
        venda.save(update_fields=["data_venda"])
        produto = venda.itens.get().produto
        produto.cst_ibs = produto.cst_cbs = "000"
        produto.classificacao_tributaria_ibs = produto.classificacao_tributaria_cbs = "000001"
        produto.aliquota_ibs_uf = Decimal("0")
        produto.aliquota_ibs_municipal = Decimal("0")
        produto.aliquota_cbs = Decimal("0")
        produto.save()

        item = NfcePayloadBuilder.build(venda, numero=2, serie=1)["items"][0]

        self.assertEqual(item["ibs_uf_aliquota"], 0.1)
        self.assertEqual(item["ibs_mun_aliquota"], 0.0)
        self.assertEqual(item["cbs_aliquota"], 0.9)

    def test_bloqueia_cst_ibs_cbs_incompativeis(self):
        venda = self.criar_venda(self.criar_cliente())
        produto = venda.itens.get().produto
        produto.cst_ibs = "000"
        produto.cst_cbs = "200"
        produto.classificacao_tributaria_ibs = "000001"
        produto.classificacao_tributaria_cbs = "000001"
        produto.save()

        with self.assertRaisesMessage(DadosInvalidosError, "unico CST"):
            NfcePayloadBuilder.build(venda, numero=1, serie=1)

    def test_cancelamento_fiscal_acontece_antes_de_cancelar_venda(self):
        venda = self.criar_venda(self.criar_cliente())
        documento = DocumentoFiscal.objects.create(
            filial=self.filial,
            tipo_documento=TipoDocumentoFiscal.NFCE,
            origem_tipo="venda_pdv",
            origem_id=venda.pk,
            numero=1,
            serie=1,
            emitente_cnpj=self.filial.cnpj,
            status=StatusDocumentoFiscal.AUTORIZADA,
            valor_total=Decimal("4.00"),
            usuario=self.usuario,
        )
        focus = Mock()

        def confirmar_cancelamento(doc, justificativa):
            doc.status = StatusDocumentoFiscal.CANCELADA
            doc.save(update_fields=["status"])
            return doc

        focus.cancelar.side_effect = confirmar_cancelamento
        with patch(
            "apps.pdv.services.cancelamento_fiscal_service._focus_service_para_filial",
            return_value=focus,
        ):
            cancelar_venda_e_documento(
                venda, self.usuario, "Erro de digitacao no produto"
            )

        venda.refresh_from_db()
        documento.refresh_from_db()
        focus.cancelar.assert_called_once()
        self.assertEqual(documento.status, StatusDocumentoFiscal.CANCELADA)
        self.assertEqual(venda.status, "cancelada")
        self.assertEqual(venda.documento_fiscal_id, documento.pk)

    def test_falha_fiscal_nao_cancela_venda(self):
        venda = self.criar_venda(self.criar_cliente())
        documento = DocumentoFiscal.objects.create(
            filial=self.filial,
            tipo_documento=TipoDocumentoFiscal.NFE,
            origem_tipo="venda_pdv",
            origem_id=venda.pk,
            numero=1,
            serie=1,
            emitente_cnpj=self.filial.cnpj,
            status=StatusDocumentoFiscal.AUTORIZADA,
            valor_total=Decimal("4.00"),
            usuario=self.usuario,
        )
        focus = Mock()
        focus.cancelar.side_effect = RuntimeError("prazo expirado")

        with patch(
            "apps.pdv.services.cancelamento_fiscal_service._focus_service_para_filial",
            return_value=focus,
        ):
            with self.assertRaisesMessage(RuntimeError, "prazo expirado"):
                cancelar_venda_e_documento(
                    venda, self.usuario, "Erro de digitacao no produto"
                )

        venda.refresh_from_db()
        self.assertEqual(venda.status, "finalizada")
        self.assertIsNone(venda.documento_fiscal_id)

    def test_nfce_nao_envia_contato_destinatario_sem_endereco(self):
        cliente = self.criar_cliente(
            celular="84994227150",
            email_nfe="cliente@example.com",
        )
        venda = self.criar_venda(cliente)

        payload = NfcePayloadBuilder.build(venda, numero=1, serie=1)

        self.assertEqual(payload["cpf_destinatario"], "04651988482")
        self.assertEqual(payload["nome_destinatario"], "THIAGO BATISTA")
        self.assertEqual(payload["indicador_inscricao_estadual_destinatario"], "9")
        self.assertNotIn("telefone_destinatario", payload)
        self.assertNotIn("email_destinatario", payload)

    def test_nfce_exige_token_focus_e_csc_configurados(self):
        from apps.core.models.parametros import ParametrosSistema

        params = ParametrosSistema.objects.create(filial=self.filial)

        with self.assertRaisesMessage(DadosInvalidosError, "Token Focus"):
            _validar_configuracao_nfce(self.filial, params)

        self.filial.focusnfe_token = "focus-token"
        self.filial.focusnfe_ambiente = 1
        self.filial.save(update_fields=["focusnfe_token", "focusnfe_ambiente"])

        with self.assertRaisesMessage(DadosInvalidosError, "CSC ID"):
            _validar_configuracao_nfce(self.filial, params)

    def test_rejeicao_hash_qrcode_traz_diagnostico_csc(self):
        documento = DocumentoFiscal.objects.create(
            filial=self.filial,
            tipo_documento=TipoDocumentoFiscal.NFCE,
            origem_tipo="venda_pdv",
            origem_id=1,
            numero=2,
            serie=1,
            emitente_cnpj=self.filial.cnpj,
            status=StatusDocumentoFiscal.PROCESSANDO,
            valor_total=Decimal("4.00"),
            usuario=self.usuario,
        )

        FocusNFeService().aplicar_retorno(documento, {
            "status": "erro_autorizacao",
            "status_sefaz": "464",
            "mensagem_sefaz": "Rejeicao: Codigo de Hash no QR-Code difere do calculado",
        })

        documento.refresh_from_db()
        self.assertEqual(documento.status, StatusDocumentoFiscal.REJEITADA)
        self.assertIn("CSC Token", documento.mensagem_sefaz)
        self.assertIn("nao envia CSC", documento.mensagem_sefaz)

    def test_status_focus_rejeitado_vira_rejeitada_no_erp(self):
        documento = DocumentoFiscal.objects.create(
            filial=self.filial,
            tipo_documento=TipoDocumentoFiscal.NFCE,
            origem_tipo="venda_pdv",
            origem_id=1,
            numero=1,
            serie=1,
            emitente_cnpj=self.filial.cnpj,
            status=StatusDocumentoFiscal.PROCESSANDO,
            valor_total=Decimal("4.00"),
            usuario=self.usuario,
        )

        FocusNFeService().aplicar_retorno(documento, {
            "status": "rejeitado",
            "status_sefaz": "999",
            "mensagem_sefaz": "Rejeicao fiscal de teste",
        })

        documento.refresh_from_db()
        self.assertEqual(documento.status, StatusDocumentoFiscal.REJEITADA)
