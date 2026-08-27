import json
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Fornecedor, FornecedorFilial
from apps.compras.models import EntradaNF, EntradaNFParcela
from apps.compras.services.compra_service import CompraService
from apps.compras.services.entrada_custo_service import EntradaCustoService
from apps.compras.services.entrada_produto_service import criar_produto_e_vincular_item
from apps.compras.services.entrada_xml_service import get_fornecedor_padrao, importar_xml_para_entrada
from apps.compras.views import (
    AdicionarItemEntradaView, CancelarEntradaView, EntradaNFConferenciaView,
    EntradaNFConsultarChaveView, EntradaNFCreateView, EntradaNFCriarProdutoItemView, EntradaNFCustosView,
    EntradaNFDetailView,
    EntradaNFFornecedorPendenteView,
    EntradaNFDiferencasView, EntradaNFFinalizacaoView, EntradaNFFinanceiroView,
    EntradaNFGerarContasPagarView, EntradaNFImportarXMLView, EntradaNFListView,
    EntradaNFLocalizarNotaView,
    EntradaNFReprocessarVinculosView, EntradaNFVincularItemView, EstornarEntradaView,
    EntradaNFVincularSugestoesView, EfetivarEntradaView, RemoverItemEntradaView,
)
from apps.core.models import Empresa, Filial, PerfilAcesso, Permissao, RegistroAuditoria, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import AlertaVencimento, Estoque, LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.estoque.views import EstoqueKardexProdutoView, EstoqueListView
from apps.financeiro.constants.enums import StatusContaPagar
from apps.financeiro.models import ContaPagar
from apps.produtos.models import (
    CategoriaProduto, CategoriaProdutoFilial, Produto, ProdutoCodigoBarras, ProdutoFilial, ProdutoFornecedorEquivalencia,
    UnidadeMedida, UnidadeMedidaFilial,
)
from apps.produtos.services.prontidao_comercial_service import contrato_pdv_produto


class EntradaRecebimentoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Entrada LTDA',
            nome_fantasia='Entrada',
            cnpj='41234567000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Entrada',
            nome_fantasia='Entrada RN',
            cnpj='41234567000192',
            uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Admin Entrada',
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='entrada-test@inoovated.com',
            nome='Usuario Entrada',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa,
            sigla='UN',
            descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)

    def setUp(self):
        self.factory = RequestFactory()

    def request(self, method, path, data=None, files=None):
        if method == 'post':
            payload = data or {}
            if files:
                payload.update(files)
            request = self.factory.post(path, payload)
        else:
            request = self.factory.get(path, data or {})
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def criar_usuario_operador(self, nome='Operador Compras', **permissoes_compras):
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa,
            nome=nome,
            is_admin=False,
        )
        usuario = Usuario.objects.create_user(
            email=f'{nome.lower().replace(" ", ".")}@inoovated.com',
            nome=nome,
            password='teste1234',
            empresa=self.empresa,
            filial=self.filial,
            perfil=perfil,
        )
        defaults = {
            'pode_ver': False,
            'pode_criar': False,
            'pode_editar': False,
            'pode_excluir': False,
            'pode_cancelar': False,
            'pode_aprovar': False,
            'pode_exportar': False,
        }
        defaults.update(permissoes_compras)
        Permissao.objects.create(perfil=perfil, modulo='compras', **defaults)
        return usuario

    def criar_fornecedor(self, documento='11222333000144'):
        fornecedor = Fornecedor.objects.create(
            filial=self.filial,
            tipo_pessoa='J',
            razao_social='Fornecedor XML',
            cpf_cnpj=documento,
            uf='RN',
        )
        FornecedorFilial.objects.create(fornecedor=fornecedor, filial=self.filial)
        return fornecedor

    def criar_produto(
        self,
        descricao='Produto XML',
        controla_lote=False,
        controla_validade=False,
        dias_aviso_vencimento=30,
    ):
        produto = Produto.objects.create(
            filial=self.filial,
            unidade_medida=self.unidade,
            descricao=descricao,
            ncm='20089900',
            controla_lote=controla_lote,
            controla_validade=controla_validade,
            dias_aviso_vencimento=dias_aviso_vencimento,
            permite_venda_sem_estoque=False,
            preco_custo=Decimal('0'),
            preco_venda=Decimal('10.00'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        return produto

    def criar_categoria(self, nome='Categoria comercial'):
        categoria = CategoriaProduto.objects.create(
            empresa=self.empresa,
            filial=self.filial,
            nome=nome,
        )
        CategoriaProdutoFilial.objects.create(categoria=categoria, filial=self.filial)
        return categoria

    def chave(self, numero='000000123', cnpj='11222333000144'):
        return f'242605{cnpj}55001{numero}1123456789'

    def xml_nfe(
        self,
        chave,
        emit_doc='11222333000144',
        dest_doc='99988877000166',
        ean='7891000000001',
        codigo='FORN-001',
        quantidade='2.0000',
        valor_unitario='30.0000',
        valor_produto='60.00',
        frete='0.00',
        seguro='0.00',
        desconto='0.00',
        outras='0.00',
        ipi='0.00',
        icms='0.00',
        icms_st='0.00',
        valor_nf=None,
        rastro_xml='',
        inf_ad_prod='',
        cobr_xml='',
    ):
        emit_tag = 'CPF' if len(emit_doc) == 11 else 'CNPJ'
        dest_tag = 'CPF' if len(dest_doc) == 11 else 'CNPJ'
        if valor_nf is None:
            valor_nf = (
                Decimal(str(valor_produto))
                + Decimal(str(frete))
                + Decimal(str(seguro))
                + Decimal(str(outras))
                + Decimal(str(ipi))
                + Decimal(str(icms_st))
                - Decimal(str(desconto))
            ).quantize(Decimal('0.01'))
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">
  <NFe>
    <infNFe Id="NFe{chave}" versao="4.00">
      <ide>
        <cUF>24</cUF>
        <serie>1</serie>
        <nNF>123</nNF>
        <dhEmi>2026-05-18T10:00:00-03:00</dhEmi>
      </ide>
      <emit>
        <{emit_tag}>{emit_doc}</{emit_tag}>
        <xNome>Fornecedor XML LTDA</xNome>
        <enderEmit>
          <xLgr>Rua Teste</xLgr>
          <nro>10</nro>
          <xMun>Natal</xMun>
          <UF>RN</UF>
          <CEP>59000000</CEP>
        </enderEmit>
      </emit>
      <dest>
        <{dest_tag}>{dest_doc}</{dest_tag}>
        <xNome>Outro Documento Operacional</xNome>
      </dest>
      <det nItem="1">
        <prod>
          <cProd>{codigo}</cProd>
          <cEAN>{ean}</cEAN>
          <xProd>Produto de fornecedor</xProd>
          <NCM>20089900</NCM>
          <uCom>CX</uCom>
          <qCom>{quantidade}</qCom>
          <vUnCom>{valor_unitario}</vUnCom>
          <vProd>{valor_produto}</vProd>
          {rastro_xml}
        </prod>
        {f'<infAdProd>{inf_ad_prod}</infAdProd>' if inf_ad_prod else ''}
      </det>
      {cobr_xml}
      <total>
        <ICMSTot>
          <vProd>{valor_produto}</vProd>
          <vFrete>{frete}</vFrete>
          <vSeg>{seguro}</vSeg>
          <vDesc>{desconto}</vDesc>
          <vOutro>{outras}</vOutro>
          <vIPI>{ipi}</vIPI>
          <vICMS>{icms}</vICMS>
          <vST>{icms_st}</vST>
          <vNF>{valor_nf}</vNF>
        </ICMSTot>
      </total>
    </infNFe>
  </NFe>
</nfeProc>'''

    def test_xml_com_destinatario_de_qualquer_documento_nao_bloqueia(self):
        entrada = importar_xml_para_entrada(
            self.xml_nfe(self.chave(), dest_doc='12345678901'),
            filial=self.filial,
            usuario=self.usuario,
            nome_arquivo='nota.xml',
        )

        item = entrada.itens.get()
        self.assertTrue(entrada.destinatario_documento_diferente)
        self.assertEqual(entrada.destinatario_documento_xml, '12345678901')
        self.assertFalse(entrada.fornecedor_pendente)
        self.assertEqual(entrada.fornecedor.cpf_cnpj, '11222333000144')
        self.assertEqual(entrada.fornecedor.razao_social, 'Fornecedor XML LTDA')
        self.assertEqual(entrada.status, EntradaNF.Status.AGUARDANDO_VINCULOS)
        self.assertIsNone(item.produto)
        self.assertTrue(item.diferenca_bloqueante)
        self.assertEqual(item.ncm_xml, '20089900')

    def test_xml_duplicado_na_mesma_filial_e_bloqueado(self):
        chave = self.chave(numero='000000124')
        importar_xml_para_entrada(
            self.xml_nfe(chave),
            filial=self.filial,
            usuario=self.usuario,
        )

        with self.assertRaisesMessage(DadosInvalidosError, 'ja foi importada'):
            importar_xml_para_entrada(
                self.xml_nfe(chave),
                filial=self.filial,
                usuario=self.usuario,
            )

    def test_xml_resolve_ean_converte_quantidade_e_movimenta_estoque(self):
        self.criar_fornecedor()
        produto = self.criar_produto()
        ProdutoCodigoBarras.objects.create(
            produto=produto,
            ean='7891000000001',
            tipo=ProdutoCodigoBarras.Tipo.FORNECEDOR,
            quantidade_conversao=Decimal('12'),
        )

        entrada = importar_xml_para_entrada(
            self.xml_nfe(self.chave(numero='000000125')),
            filial=self.filial,
            usuario=self.usuario,
        )
        item = entrada.itens.get()

        self.assertEqual(entrada.status, EntradaNF.Status.AGUARDANDO_CONFERENCIA)
        self.assertEqual(item.produto, produto)
        self.assertEqual(item.quantidade_xml, Decimal('2.000'))
        self.assertEqual(item.quantidade_estoque, Decimal('24.000'))
        self.assertEqual(item.valor_unitario, Decimal('2.5000'))

        CompraService.efetivar_entrada(entrada, self.usuario)

        entrada.refresh_from_db()
        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(entrada.status, EntradaNF.Status.EFETIVADA)
        self.assertEqual(estoque.quantidade_atual, Decimal('24.000'))
        self.assertEqual(estoque.quantidade_disponivel, Decimal('24.000'))

    def test_xml_importa_rastro_lote_validade_e_efetiva_com_lote_rastreavel(self):
        self.criar_fornecedor()
        validade = timezone.localdate() + timedelta(days=90)
        fabricacao = timezone.localdate() - timedelta(days=10)
        produto = self.criar_produto(
            'Produto rastreado XML',
            controla_lote=True,
            controla_validade=True,
        )
        ProdutoCodigoBarras.objects.create(
            produto=produto,
            ean='7891000000001',
            tipo=ProdutoCodigoBarras.Tipo.FORNECEDOR,
            quantidade_conversao=Decimal('1'),
        )

        entrada = importar_xml_para_entrada(
            self.xml_nfe(
                self.chave(numero='000000135'),
                quantidade='2.0000',
                valor_unitario='30.0000',
                valor_produto='60.00',
                rastro_xml=f'''
          <rastro>
            <nLote>XML-LOTE-01</nLote>
            <qLote>2.0000</qLote>
            <dFab>{fabricacao:%Y-%m-%d}</dFab>
            <dVal>{validade:%Y-%m-%d}</dVal>
          </rastro>''',
            ),
            filial=self.filial,
            usuario=self.usuario,
        )
        item = entrada.itens.get()

        self.assertEqual(entrada.status, EntradaNF.Status.AGUARDANDO_CONFERENCIA)
        self.assertEqual(item.numero_lote, 'XML-LOTE-01')
        self.assertEqual(item.data_fabricacao, fabricacao)
        self.assertEqual(item.data_validade, validade)
        self.assertFalse(item.diferenca_bloqueante)

        CompraService.efetivar_entrada(entrada, self.usuario)
        item.refresh_from_db()
        lote = LoteProduto.objects.get(produto=produto, filial=self.filial)
        movimento = MovimentacaoEstoque.objects.get(
            produto=produto,
            filial=self.filial,
            documento_id=entrada.pk,
        )

        self.assertEqual(item.lote_gerado, lote)
        self.assertEqual(movimento.lote, lote)
        self.assertEqual(lote.numero_lote, 'XML-LOTE-01')
        self.assertEqual(lote.data_validade, validade)
        self.assertEqual(lote.quantidade_atual, Decimal('2.000'))

    def test_xml_importa_lote_validade_em_inf_ad_prod(self):
        self.criar_fornecedor()
        produto = self.criar_produto(
            'Produto lote texto XML',
            controla_lote=True,
            controla_validade=True,
        )
        ProdutoCodigoBarras.objects.create(
            produto=produto,
            ean='7891000000001',
            tipo=ProdutoCodigoBarras.Tipo.FORNECEDOR,
            quantidade_conversao=Decimal('1'),
        )

        entrada = importar_xml_para_entrada(
            self.xml_nfe(
                self.chave(numero='000000139'),
                quantidade='3.0000',
                valor_unitario='10.0000',
                valor_produto='30.00',
                inf_ad_prod='Fonte IBPT / Lote: 010/26 - Fab.: 07/08/2025 - Val.: 31/08/2026',
            ),
            filial=self.filial,
            usuario=self.usuario,
        )
        item = entrada.itens.get()

        self.assertEqual(item.numero_lote, '010/26')
        self.assertEqual(item.data_fabricacao, date(2025, 8, 7))
        self.assertEqual(item.data_validade, date(2026, 8, 31))
        self.assertFalse(item.diferenca_bloqueante)

        CompraService.efetivar_entrada(entrada, self.usuario)
        lote = LoteProduto.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(lote.numero_lote, '010/26')
        self.assertEqual(lote.data_validade, date(2026, 8, 31))
        self.assertEqual(lote.quantidade_atual, Decimal('3.000'))

    def test_composicao_custo_rateia_frete_st_desconto_e_efetiva_lote(self):
        self.criar_fornecedor()
        validade = timezone.localdate() + timedelta(days=120)
        produto = self.criar_produto(
            'Produto custo composto',
            controla_lote=True,
            controla_validade=True,
        )
        ProdutoCodigoBarras.objects.create(
            produto=produto,
            ean='7891000000001',
            tipo=ProdutoCodigoBarras.Tipo.FORNECEDOR,
            quantidade_conversao=Decimal('1'),
        )

        entrada = importar_xml_para_entrada(
            self.xml_nfe(
                self.chave(numero='000000935'),
                quantidade='10.0000',
                valor_unitario='10.0000',
                valor_produto='100.00',
                frete='10.00',
                seguro='2.00',
                outras='3.00',
                desconto='5.00',
                ipi='4.00',
                icms='12.00',
                icms_st='6.00',
                rastro_xml=f'''
          <rastro>
            <nLote>CUSTO-LOTE-01</nLote>
            <qLote>10.0000</qLote>
            <dVal>{validade:%Y-%m-%d}</dVal>
          </rastro>''',
            ),
            filial=self.filial,
            usuario=self.usuario,
        )

        composicao = EntradaCustoService.compor(
            entrada,
            metodo_rateio=EntradaNF.MetodoRateioCusto.VALOR,
            incluir_ipi=True,
            incluir_icms_st=True,
            incluir_icms=False,
            salvar=True,
            salvar_configuracao=True,
        )
        item = entrada.itens.get()

        self.assertEqual(entrada.valor_icms_st, Decimal('6.00'))
        self.assertEqual(composicao['resumo']['custo_total'], Decimal('120.00'))
        item.refresh_from_db()
        self.assertEqual(item.custo_unitario_total, Decimal('12.0000'))

        CompraService.efetivar_entrada(entrada, self.usuario)

        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        lote = LoteProduto.objects.get(produto=produto, filial=self.filial, numero_lote='CUSTO-LOTE-01')
        movimento = MovimentacaoEstoque.objects.get(produto=produto, documento_id=entrada.pk)
        self.assertEqual(estoque.custo_medio, Decimal('12.0000'))
        self.assertEqual(lote.custo_unitario, Decimal('12.0000'))
        self.assertEqual(movimento.valor_unitario, Decimal('12.0000'))

    def test_tela_custos_simula_e_aplica_icms_nao_recuperavel(self):
        self.criar_fornecedor()
        produto = self.criar_produto('Produto custo tela')
        ProdutoCodigoBarras.objects.create(
            produto=produto,
            ean='7891000000001',
            tipo=ProdutoCodigoBarras.Tipo.FORNECEDOR,
            quantidade_conversao=Decimal('1'),
        )
        entrada = importar_xml_para_entrada(
            self.xml_nfe(
                self.chave(numero='000000936'),
                quantidade='5.0000',
                valor_unitario='20.0000',
                valor_produto='100.00',
                frete='10.00',
                icms='12.00',
            ),
            filial=self.filial,
            usuario=self.usuario,
        )

        request_get = self.request('get', reverse('compras:entrada-custos', args=[entrada.pk]))
        response = EntradaNFCustosView.as_view()(request_get, pk=entrada.pk)
        self.assertContains(response, 'Composicao de custo')
        self.assertContains(response, 'ICMS nao recuperavel')
        self.assertContains(response, 'Impostos recuperaveis')
        self.assertContains(response, 'Custo total dos produtos')
        self.assertContains(response, 'Diferenca contra total da nota')

        request_post = self.request('post', reverse('compras:entrada-custos', args=[entrada.pk]), {
            'metodo_rateio': EntradaNF.MetodoRateioCusto.VALOR,
            'custo_financeiro': '0.00',
            'incluir_ipi': '1',
            'incluir_icms_st': '1',
            'incluir_icms': '1',
        })
        response = EntradaNFCustosView.as_view()(request_post, pk=entrada.pk)
        self.assertEqual(response.status_code, 302)

        entrada.refresh_from_db()
        item = entrada.itens.get()
        self.assertTrue(entrada.custo_incluir_icms)
        self.assertEqual(item.custo_unitario_total, Decimal('24.4000'))

    def test_tela_custos_permite_editar_componentes_e_recalcula(self):
        self.criar_fornecedor()
        produto = self.criar_produto('Produto custo editavel')
        ProdutoCodigoBarras.objects.create(
            produto=produto,
            ean='7891000000001',
            tipo=ProdutoCodigoBarras.Tipo.FORNECEDOR,
            quantidade_conversao=Decimal('1'),
        )
        entrada = importar_xml_para_entrada(
            self.xml_nfe(
                self.chave(numero='000000937'),
                quantidade='5.0000',
                valor_unitario='20.0000',
                valor_produto='100.00',
            ),
            filial=self.filial,
            usuario=self.usuario,
        )

        request_post = self.request('post', reverse('compras:entrada-custos', args=[entrada.pk]), {
            'acao': 'salvar_componentes',
            'valor_frete': '15,00',
            'valor_seguro': '2,00',
            'valor_outras_despesas': '3,00',
            'valor_desconto': '5,00',
            'valor_ipi': '4,00',
            'valor_icms_st': '6,00',
            'valor_icms': '12,00',
        })
        response = EntradaNFCustosView.as_view()(request_post, pk=entrada.pk)
        self.assertEqual(response.status_code, 302)

        entrada.refresh_from_db()
        item = entrada.itens.get()
        self.assertEqual(entrada.valor_total, Decimal('125.00'))
        self.assertEqual(entrada.valor_icms, Decimal('12.00'))
        self.assertFalse(entrada.custo_incluir_icms)
        self.assertEqual(item.custo_unitario_total, Decimal('25.0000'))

    def test_tela_custos_exibe_alertas_de_st_frete_icms_e_fallback_peso(self):
        fornecedor = self.criar_fornecedor(documento='44555666000181')
        produto = self.criar_produto('Produto sem peso custo')
        entrada = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=fornecedor,
            numero_nf='NF-CUSTO-ALERTAS',
            serie_nf='1',
            origem_entrada=EntradaNF.OrigemEntrada.MANUAL,
            data_emissao_nf=timezone.localdate(),
            data_entrada=timezone.now(),
            status=EntradaNF.Status.CONFERIDA,
            usuario=self.usuario,
            valor_produtos=Decimal('100.00'),
            valor_frete=Decimal('10.00'),
            valor_icms_st=Decimal('5.00'),
            valor_icms=Decimal('12.00'),
            valor_total=Decimal('115.00'),
        )
        entrada.itens.create(
            produto=produto,
            numero_item=1,
            quantidade=Decimal('10'),
            quantidade_xml=Decimal('10'),
            quantidade_estoque=Decimal('10'),
            quantidade_recebida=Decimal('10'),
            unidade_xml='UN',
            unidade_estoque='UN',
            valor_unitario=Decimal('10.00'),
            valor_bruto=Decimal('100.00'),
            valor_total=Decimal('100.00'),
        )

        request_get = self.request('get', reverse('compras:entrada-custos', args=[entrada.pk]), {
            'metodo_rateio': EntradaNF.MetodoRateioCusto.PESO,
            'incluir_ipi': '1',
            'incluir_icms_st': '0',
            'incluir_icms': '1',
        })
        response = EntradaNFCustosView.as_view()(request_get, pk=entrada.pk)

        self.assertContains(response, 'ICMS marcado como custo, confirme se e nao recuperavel.')
        self.assertContains(response, 'ST sem inclusao no custo.')
        self.assertContains(response, 'Frete informado mas nao revisado.')
        self.assertContains(response, 'Produto sem peso usando fallback de rateio.')

    def test_custo_calcula_cenarios_fiscais_e_bloqueia_negativo(self):
        fornecedor = self.criar_fornecedor(documento='44555666000182')
        produto = self.criar_produto('Produto cenarios fiscais')
        entrada = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=fornecedor,
            numero_nf='NF-CUSTO-CENARIOS',
            serie_nf='1',
            origem_entrada=EntradaNF.OrigemEntrada.MANUAL,
            data_emissao_nf=timezone.localdate(),
            data_entrada=timezone.now(),
            status=EntradaNF.Status.CONFERIDA,
            usuario=self.usuario,
            valor_produtos=Decimal('100.00'),
            valor_frete=Decimal('10.00'),
            valor_desconto=Decimal('4.00'),
            valor_ipi=Decimal('6.00'),
            valor_icms_st=Decimal('8.00'),
            valor_icms=Decimal('12.00'),
            valor_total=Decimal('120.00'),
        )
        entrada.itens.create(
            produto=produto,
            numero_item=1,
            quantidade=Decimal('10'),
            quantidade_xml=Decimal('10'),
            quantidade_estoque=Decimal('10'),
            quantidade_recebida=Decimal('10'),
            unidade_xml='UN',
            unidade_estoque='UN',
            valor_unitario=Decimal('10.00'),
            valor_bruto=Decimal('100.00'),
            valor_total=Decimal('100.00'),
        )

        frete_st = EntradaCustoService.compor(
            entrada,
            incluir_ipi=False,
            incluir_icms_st=True,
            incluir_icms=False,
        )
        self.assertEqual(frete_st['resumo']['custo_total'], Decimal('114.00'))

        desconto_ipi = EntradaCustoService.compor(
            entrada,
            incluir_ipi=True,
            incluir_icms_st=False,
            incluir_icms=False,
        )
        self.assertEqual(desconto_ipi['resumo']['custo_total'], Decimal('112.00'))

        icms_recuperavel = EntradaCustoService.compor(
            entrada,
            incluir_ipi=False,
            incluir_icms_st=False,
            incluir_icms=False,
        )
        self.assertEqual(icms_recuperavel['resumo']['custo_total'], Decimal('106.00'))
        self.assertEqual(icms_recuperavel['resumo']['icms_nao_recuperavel'], Decimal('0'))

        icms_nao_recuperavel = EntradaCustoService.compor(
            entrada,
            incluir_ipi=False,
            incluir_icms_st=False,
            incluir_icms=True,
        )
        self.assertEqual(icms_nao_recuperavel['resumo']['custo_total'], Decimal('118.00'))
        self.assertEqual(icms_nao_recuperavel['resumo']['icms_nao_recuperavel'], Decimal('12.00'))

        entrada.valor_desconto = Decimal('200.00')
        entrada.save(update_fields=['valor_desconto', 'updated_at'])
        with self.assertRaisesMessage(DadosInvalidosError, 'Composicao de custo negativa'):
            EntradaCustoService.compor(entrada, incluir_ipi=False, incluir_icms_st=False)

    def test_finalizacao_exige_confirmacao_para_custo_critico(self):
        fornecedor = self.criar_fornecedor(documento='44555666000177')
        produto = self.criar_produto('Produto custo critico')
        produto.preco_custo = Decimal('10.00')
        produto.save(update_fields=['preco_custo', 'updated_at'])
        entrada = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=fornecedor,
            numero_nf='NF-CUSTO-CRITICO',
            serie_nf='1',
            origem_entrada=EntradaNF.OrigemEntrada.MANUAL,
            data_emissao_nf=timezone.localdate(),
            data_entrada=timezone.now(),
            status=EntradaNF.Status.CONFERIDA,
            usuario=self.usuario,
            valor_produtos=Decimal('200.00'),
            valor_total=Decimal('200.00'),
        )
        entrada.itens.create(
            produto=produto,
            numero_item=1,
            quantidade=Decimal('10'),
            quantidade_xml=Decimal('10'),
            quantidade_estoque=Decimal('10'),
            quantidade_recebida=Decimal('10'),
            unidade_xml='UN',
            unidade_estoque='UN',
            valor_unitario=Decimal('20.00'),
            valor_bruto=Decimal('200.00'),
            valor_total=Decimal('200.00'),
        )

        path = reverse('compras:entrada-finalizacao', args=[entrada.pk])
        request_get = self.request('get', path)
        response = EntradaNFFinalizacaoView.as_view()(request_get, pk=entrada.pk)
        self.assertContains(response, 'confirmar_custo_critico')
        self.assertContains(response, 'Custo critico requer confirmacao')

        with self.assertRaisesMessage(DadosInvalidosError, 'Custo critico exige confirmacao'):
            CompraService.efetivar_entrada(entrada, self.usuario)

        CompraService.efetivar_entrada(
            entrada,
            self.usuario,
            confirmar_custo_critico=True,
        )
        entrada.refresh_from_db()
        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(entrada.status, EntradaNF.Status.EFETIVADA)
        self.assertEqual(estoque.custo_medio, Decimal('20.0000'))

    def test_custo_rateia_frete_por_valor_quantidade_e_peso(self):
        fornecedor = self.criar_fornecedor(documento='44555666000178')
        produto_a = self.criar_produto('Produto rateio A')
        produto_b = self.criar_produto('Produto rateio B')
        produto_a.peso_liquido = Decimal('2.000')
        produto_b.peso_liquido = Decimal('1.000')
        produto_a.save(update_fields=['peso_liquido', 'updated_at'])
        produto_b.save(update_fields=['peso_liquido', 'updated_at'])
        entrada = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=fornecedor,
            numero_nf='NF-RATEIO',
            serie_nf='1',
            origem_entrada=EntradaNF.OrigemEntrada.MANUAL,
            data_emissao_nf=timezone.localdate(),
            data_entrada=timezone.now(),
            status=EntradaNF.Status.CONFERIDA,
            usuario=self.usuario,
            valor_produtos=Decimal('400.00'),
            valor_frete=Decimal('30.00'),
            valor_total=Decimal('430.00'),
        )
        entrada.itens.create(
            produto=produto_a,
            numero_item=1,
            quantidade=Decimal('10'),
            quantidade_xml=Decimal('10'),
            quantidade_estoque=Decimal('10'),
            quantidade_recebida=Decimal('10'),
            unidade_xml='UN',
            unidade_estoque='UN',
            valor_unitario=Decimal('10.00'),
            valor_bruto=Decimal('100.00'),
            valor_total=Decimal('100.00'),
        )
        entrada.itens.create(
            produto=produto_b,
            numero_item=2,
            quantidade=Decimal('10'),
            quantidade_xml=Decimal('10'),
            quantidade_estoque=Decimal('10'),
            quantidade_recebida=Decimal('10'),
            unidade_xml='UN',
            unidade_estoque='UN',
            valor_unitario=Decimal('30.00'),
            valor_bruto=Decimal('300.00'),
            valor_total=Decimal('300.00'),
        )

        por_valor = EntradaCustoService.compor(
            entrada,
            metodo_rateio=EntradaNF.MetodoRateioCusto.VALOR,
        )['linhas']
        por_quantidade = EntradaCustoService.compor(
            entrada,
            metodo_rateio=EntradaNF.MetodoRateioCusto.QUANTIDADE,
        )['linhas']
        por_peso = EntradaCustoService.compor(
            entrada,
            metodo_rateio=EntradaNF.MetodoRateioCusto.PESO,
        )['linhas']

        self.assertEqual([linha.frete for linha in por_valor], [Decimal('7.50'), Decimal('22.50')])
        self.assertEqual([linha.frete for linha in por_quantidade], [Decimal('15.00'), Decimal('15.00')])
        self.assertEqual([linha.frete for linha in por_peso], [Decimal('20.00'), Decimal('10.00')])

    def test_custo_rateio_por_peso_zerado_cai_para_quantidade(self):
        fornecedor = self.criar_fornecedor(documento='44555666000183')
        produto_a = self.criar_produto('Produto peso zerado A')
        produto_b = self.criar_produto('Produto peso zerado B')
        entrada = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=fornecedor,
            numero_nf='NF-RATEIO-PESO-ZERADO',
            serie_nf='1',
            origem_entrada=EntradaNF.OrigemEntrada.MANUAL,
            data_emissao_nf=timezone.localdate(),
            data_entrada=timezone.now(),
            status=EntradaNF.Status.CONFERIDA,
            usuario=self.usuario,
            valor_produtos=Decimal('200.00'),
            valor_frete=Decimal('30.00'),
            valor_total=Decimal('230.00'),
        )
        for numero, produto in enumerate([produto_a, produto_b], start=1):
            entrada.itens.create(
                produto=produto,
                numero_item=numero,
                quantidade=Decimal('10'),
                quantidade_xml=Decimal('10'),
                quantidade_estoque=Decimal('10'),
                quantidade_recebida=Decimal('10'),
                unidade_xml='UN',
                unidade_estoque='UN',
                valor_unitario=Decimal('10.00'),
                valor_bruto=Decimal('100.00'),
                valor_total=Decimal('100.00'),
            )

        composicao = EntradaCustoService.compor(
            entrada,
            metodo_rateio=EntradaNF.MetodoRateioCusto.PESO,
        )
        self.assertEqual(composicao['metodo_efetivo'], EntradaNF.MetodoRateioCusto.QUANTIDADE)
        self.assertEqual([linha.frete for linha in composicao['linhas']], [Decimal('15.00'), Decimal('15.00')])

    def test_finalizacao_bloqueia_custo_composto_sem_confirmacao(self):
        fornecedor = self.criar_fornecedor(documento='44555666000179')
        produto = self.criar_produto('Produto custo composto')
        entrada = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=fornecedor,
            numero_nf='NF-CUSTO-COMPOSTO',
            serie_nf='1',
            origem_entrada=EntradaNF.OrigemEntrada.MANUAL,
            data_emissao_nf=timezone.localdate(),
            data_entrada=timezone.now(),
            status=EntradaNF.Status.CONFERIDA,
            usuario=self.usuario,
            valor_produtos=Decimal('100.00'),
            valor_frete=Decimal('10.00'),
            valor_icms_st=Decimal('5.00'),
            valor_desconto=Decimal('2.00'),
            valor_total=Decimal('113.00'),
        )
        entrada.itens.create(
            produto=produto,
            numero_item=1,
            quantidade=Decimal('10'),
            quantidade_xml=Decimal('10'),
            quantidade_estoque=Decimal('10'),
            quantidade_recebida=Decimal('10'),
            unidade_xml='UN',
            unidade_estoque='UN',
            valor_unitario=Decimal('10.00'),
            valor_bruto=Decimal('100.00'),
            valor_total=Decimal('100.00'),
        )

        request_get = self.request('get', reverse('compras:entrada-finalizacao', args=[entrada.pk]))
        response = EntradaNFFinalizacaoView.as_view()(request_get, pk=entrada.pk)
        self.assertContains(response, 'Resumo do custo antes de efetivar')
        self.assertContains(response, 'Resumo antes de efetivar')
        self.assertContains(response, 'Produtos e movimentacao')
        self.assertContains(response, 'Lotes e validade')
        self.assertContains(response, 'Alertas que permitem seguir com confirmacao')
        self.assertContains(response, 'confirmar_resumo_final')
        self.assertContains(response, 'Acrescimos')
        self.assertContains(response, 'Dif. nota')

        request_sem_confirmacao = self.request(
            'post',
            reverse('compras:entrada-efetivar', args=[entrada.pk]),
        )
        response = EfetivarEntradaView.as_view()(request_sem_confirmacao, pk=entrada.pk)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('compras:entrada-finalizacao', args=[entrada.pk]))
        entrada.refresh_from_db()
        self.assertEqual(entrada.status, EntradaNF.Status.CONFERIDA)

        request_sem_confirmacao_custo = self.request(
            'post',
            reverse('compras:entrada-efetivar', args=[entrada.pk]),
            {'confirmar_resumo_final': '1'},
        )
        response = EfetivarEntradaView.as_view()(request_sem_confirmacao_custo, pk=entrada.pk)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('compras:entrada-finalizacao', args=[entrada.pk]))
        entrada.refresh_from_db()
        self.assertEqual(entrada.status, EntradaNF.Status.CONFERIDA)

        request_confirmada = self.request(
            'post',
            reverse('compras:entrada-efetivar', args=[entrada.pk]),
            {'confirmar_resumo_final': '1', 'confirmar_custo_composto': '1'},
        )
        response = EfetivarEntradaView.as_view()(request_confirmada, pk=entrada.pk)
        self.assertEqual(response.status_code, 302)
        entrada.refresh_from_db()
        self.assertEqual(entrada.status, EntradaNF.Status.EFETIVADA)

    def test_efetivacao_cria_auditoria_e_aparece_no_detalhe(self):
        fornecedor = self.criar_fornecedor(documento='44555666000190')
        produto = self.criar_produto('Produto auditoria entrada')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-AUD-001',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('2'),
            valor_unitario=Decimal('10'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
        )

        request = self.request(
            'post',
            reverse('compras:entrada-efetivar', args=[entrada.pk]),
            {'confirmar_resumo_final': '1'},
        )
        response = EfetivarEntradaView.as_view()(request, pk=entrada.pk)

        self.assertEqual(response.status_code, 302)
        log = RegistroAuditoria.objects.get(
            modulo='compras',
            acao='efetivar',
            objeto_tipo='compras.entradanf',
            objeto_id=entrada.pk,
        )
        self.assertEqual(log.usuario, self.usuario)
        self.assertEqual(log.filial, self.filial)
        self.assertEqual(log.metadados['produtos_movimentados'], 1)

        request_get = self.request('get', reverse('compras:entrada-detail', args=[entrada.pk]))
        response = EntradaNFDetailView.as_view()(request_get, pk=entrada.pk)
        self.assertNotContains(response, 'Auditoria da entrada')
        self.assertContains(response, 'efetivada')

    def test_estorno_entrada_com_saldo_disponivel_cria_movimento_reverso_e_auditoria(self):
        fornecedor = self.criar_fornecedor(documento='44555666000201')
        produto = self.criar_produto('Produto estorno entrada')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-EST-OK',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('3'),
            valor_unitario=Decimal('12'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
        )
        CompraService.efetivar_entrada(entrada, self.usuario)

        request = self.request(
            'post',
            reverse('compras:entrada-estorno', args=[entrada.pk]),
            {'motivo': 'Entrada duplicada no recebimento'},
        )
        response = EstornarEntradaView.as_view()(request, pk=entrada.pk)

        entrada.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(entrada.status, EntradaNF.Status.ESTORNADA)
        self.assertIsNotNone(entrada.data_estorno)
        self.assertEqual(entrada.usuario_estorno, self.usuario)
        self.assertTrue(MovimentacaoEstoque.objects.filter(
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.ESTORNO_ENTRADA,
            documento_id=entrada.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
            quantidade=Decimal('3'),
        ).exists())
        estoque = Estoque.objects.get(filial=self.filial, produto=produto)
        self.assertEqual(estoque.quantidade_atual, Decimal('0.000'))
        self.assertEqual(estoque.custo_medio, Decimal('0'))
        log = RegistroAuditoria.objects.get(
            modulo='compras',
            acao='cancelar',
            objeto_tipo='compras.entradanf',
            objeto_id=entrada.pk,
        )
        self.assertEqual(log.justificativa, 'Entrada duplicada no recebimento')

        request_get = self.request('get', reverse('compras:entrada-detail', args=[entrada.pk]))
        response = EntradaNFDetailView.as_view()(request_get, pk=entrada.pk)
        self.assertContains(response, 'Entrada estornada')
        self.assertContains(response, 'Movimentos de estorno')

    def test_estorno_bloqueia_quando_lote_foi_consumido(self):
        fornecedor = self.criar_fornecedor(documento='44555666000202')
        produto = self.criar_produto('Produto lote estorno', controla_lote=True)
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-EST-LOTE',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        item = CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('5'),
            valor_unitario=Decimal('8'),
            numero_lote='LT-EST',
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
        )
        CompraService.efetivar_entrada(entrada, self.usuario)
        lote = entrada.itens.get().lote_gerado
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
            quantidade=Decimal('1'),
            usuario_id=self.usuario.pk,
            lote_id=lote.pk,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
            observacao='Consumo posterior ao recebimento',
        )

        request = self.request(
            'post',
            reverse('compras:entrada-estorno', args=[entrada.pk]),
            {'motivo': 'Erro na nota'},
        )
        response = EstornarEntradaView.as_view()(request, pk=entrada.pk)

        entrada.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('compras:entrada-estorno', args=[entrada.pk]))
        self.assertEqual(entrada.status, EntradaNF.Status.EFETIVADA)
        self.assertFalse(MovimentacaoEstoque.objects.filter(
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.ESTORNO_ENTRADA,
            documento_id=entrada.pk,
        ).exists())

    def test_estorno_exige_permissao_justificativa_e_nao_reestorna(self):
        operador = self.criar_usuario_operador('Operador sem estorno', pode_ver=True)
        fornecedor = self.criar_fornecedor(documento='44555666000203')
        produto = self.criar_produto('Produto estorno permissao')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-EST-PERM',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('1'),
            valor_unitario=Decimal('9'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
        )
        CompraService.efetivar_entrada(entrada, self.usuario)

        request_sem_permissao = self.request(
            'post',
            reverse('compras:entrada-estorno', args=[entrada.pk]),
            {'motivo': 'Sem permissao'},
        )
        request_sem_permissao.user = operador
        response = EstornarEntradaView.as_view()(request_sem_permissao, pk=entrada.pk)
        self.assertEqual(response.url, reverse('core:dashboard'))

        request_sem_motivo = self.request('post', reverse('compras:entrada-estorno', args=[entrada.pk]), {})
        response = EstornarEntradaView.as_view()(request_sem_motivo, pk=entrada.pk)
        self.assertEqual(response.url, reverse('compras:entrada-estorno', args=[entrada.pk]))
        entrada.refresh_from_db()
        self.assertEqual(entrada.status, EntradaNF.Status.EFETIVADA)

        request = self.request(
            'post',
            reverse('compras:entrada-estorno', args=[entrada.pk]),
            {'motivo': 'Primeiro estorno valido'},
        )
        EstornarEntradaView.as_view()(request, pk=entrada.pk)
        entrada.refresh_from_db()
        self.assertEqual(entrada.status, EntradaNF.Status.ESTORNADA)

        request_reestorno = self.request(
            'post',
            reverse('compras:entrada-estorno', args=[entrada.pk]),
            {'motivo': 'Tentativa duplicada'},
        )
        response = EstornarEntradaView.as_view()(request_reestorno, pk=entrada.pk)
        self.assertEqual(response.url, reverse('compras:entrada-estorno', args=[entrada.pk]))
        self.assertEqual(MovimentacaoEstoque.objects.filter(
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.ESTORNO_ENTRADA,
            documento_id=entrada.pk,
        ).count(), 1)

    def test_estorno_cancela_contas_a_pagar_abertas(self):
        fornecedor = self.criar_fornecedor(documento='44555666000204')
        produto = self.criar_produto('Produto estorno financeiro')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-EST-FIN',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('2'),
            valor_unitario=Decimal('10'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
        )
        EntradaNFParcela.objects.create(
            entrada=entrada,
            numero='001',
            data_vencimento=timezone.localdate(),
            valor=Decimal('20.00'),
            origem=EntradaNFParcela.Origem.MANUAL,
        )
        CompraService.efetivar_entrada(entrada, self.usuario)
        EntradaNFGerarContasPagarView.as_view()(
            self.request('post', reverse('compras:entrada-gerar-contas-pagar', args=[entrada.pk])),
            pk=entrada.pk,
        )
        conta = ContaPagar.objects.get()
        self.assertEqual(conta.status, StatusContaPagar.ABERTO)

        request = self.request(
            'post',
            reverse('compras:entrada-estorno', args=[entrada.pk]),
            {'motivo': 'Nota cancelada pelo fornecedor'},
        )
        EstornarEntradaView.as_view()(request, pk=entrada.pk)

        conta.refresh_from_db()
        parcela = entrada.parcelas_financeiras.get()
        self.assertEqual(conta.status, StatusContaPagar.CANCELADO)
        self.assertEqual(parcela.status, EntradaNFParcela.Status.CANCELADA)

    def test_finalizacao_prioriza_itens_problematicos_e_separa_bloqueios(self):
        fornecedor = self.criar_fornecedor(documento='44555666000184')
        produto = self.criar_produto(
            'Produto lote finalizacao',
            controla_lote=True,
            controla_validade=True,
        )
        entrada = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=fornecedor,
            numero_nf='NF-REVISAO-FINAL',
            serie_nf='1',
            origem_entrada=EntradaNF.OrigemEntrada.MANUAL,
            data_emissao_nf=timezone.localdate(),
            data_entrada=timezone.now(),
            status=EntradaNF.Status.CONFERIDA,
            usuario=self.usuario,
            valor_produtos=Decimal('150.00'),
            valor_total=Decimal('150.00'),
        )
        entrada.itens.create(
            produto=produto,
            numero_item=1,
            quantidade=Decimal('10'),
            quantidade_xml=Decimal('10'),
            quantidade_estoque=Decimal('10'),
            quantidade_recebida=Decimal('10'),
            unidade_xml='UN',
            unidade_estoque='UN',
            valor_unitario=Decimal('10.00'),
            valor_bruto=Decimal('100.00'),
            valor_total=Decimal('100.00'),
        )
        entrada.itens.create(
            numero_item=2,
            codigo_produto_fornecedor='SEM-PROD',
            descricao_xml='Item sem produto finalizacao',
            quantidade=Decimal('5'),
            quantidade_xml=Decimal('5'),
            quantidade_estoque=Decimal('5'),
            quantidade_recebida=Decimal('5'),
            unidade_xml='UN',
            unidade_estoque='UN',
            valor_unitario=Decimal('10.00'),
            valor_bruto=Decimal('50.00'),
            valor_total=Decimal('50.00'),
        )

        request_get = self.request('get', reverse('compras:entrada-finalizacao', args=[entrada.pk]))
        response = EntradaNFFinalizacaoView.as_view()(request_get, pk=entrada.pk)

        self.assertContains(response, 'Bloqueado para efetivar')
        self.assertContains(response, 'Itens que precisam de atencao')
        self.assertContains(response, 'Sem produto interno')
        self.assertContains(response, 'Lote obrigatorio pendente')
        self.assertContains(response, 'Pendencias que impedem finalizar')
        self.assertContains(response, 'Resolver pendencias')
        self.assertNotContains(response, 'confirmar_resumo_final')

    def test_conferencia_exibe_status_operacionais_da_entrada(self):
        fornecedor = self.criar_fornecedor(documento='44555666000180')
        produto = self.criar_produto(
            'Produto status conferencia',
            controla_lote=True,
            controla_validade=True,
        )
        self.criar_produto('Produto sugerido conferencia')
        produto.preco_custo = Decimal('10.00')
        produto.save(update_fields=['preco_custo', 'updated_at'])
        entrada = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=fornecedor,
            numero_nf='NF-STATUS-CONF',
            serie_nf='1',
            origem_entrada=EntradaNF.OrigemEntrada.MANUAL,
            data_emissao_nf=timezone.localdate(),
            data_entrada=timezone.now(),
            status=EntradaNF.Status.AGUARDANDO_CONFERENCIA,
            usuario=self.usuario,
            valor_produtos=Decimal('250.00'),
            valor_total=Decimal('250.00'),
        )
        entrada.itens.create(
            produto=produto,
            numero_item=1,
            quantidade=Decimal('10'),
            quantidade_xml=Decimal('10'),
            quantidade_estoque=Decimal('10'),
            quantidade_recebida=Decimal('10'),
            unidade_xml='UN',
            unidade_estoque='UN',
            valor_unitario=Decimal('20.00'),
            valor_bruto=Decimal('200.00'),
            valor_total=Decimal('200.00'),
        )
        entrada.itens.create(
            numero_item=2,
            codigo_produto_fornecedor='SEM-CAD',
            descricao_xml='Item sem qualquer cadastro semelhante',
            quantidade=Decimal('5'),
            quantidade_xml=Decimal('5'),
            quantidade_estoque=Decimal('5'),
            quantidade_recebida=Decimal('3'),
            unidade_xml='UN',
            unidade_estoque='UN',
            valor_unitario=Decimal('10.00'),
            valor_bruto=Decimal('50.00'),
            valor_total=Decimal('50.00'),
        )
        entrada.itens.create(
            numero_item=3,
            codigo_produto_fornecedor='SUG-001',
            descricao_xml='Produto sugerido conferencia',
            quantidade=Decimal('2'),
            quantidade_xml=Decimal('2'),
            quantidade_estoque=Decimal('2'),
            quantidade_recebida=Decimal('2'),
            unidade_xml='UN',
            unidade_estoque='UN',
            valor_unitario=Decimal('25.00'),
            valor_bruto=Decimal('50.00'),
            valor_total=Decimal('50.00'),
        )

        request = self.request('get', reverse('compras:entrada-conferencia', args=[entrada.pk]))
        response = EntradaNFConferenciaView.as_view()(request, pk=entrada.pk)

        self.assertContains(response, 'data-status-card="vinculados" data-status-count="1"')
        self.assertContains(response, 'data-status-card="sugeridos" data-status-count="1"')
        self.assertContains(response, 'data-status-card="sem_produto" data-status-count="1"')
        self.assertContains(response, 'data-status-card="divergencias" data-status-count="1"')
        self.assertContains(response, 'data-status-card="lote_pendente" data-status-count="1"')
        self.assertContains(response, 'data-status-card="custo_critico" data-status-count="1"')
        self.assertContains(response, 'Vinculado')
        self.assertContains(response, 'Sugerido')
        self.assertContains(response, 'Sem produto')
        self.assertContains(response, 'Divergencia')
        self.assertContains(response, 'Lote pendente')
        self.assertContains(response, 'Custo critico')
        self.assertContains(response, 'entrada-row-status-critico')
        self.assertContains(response, 'entrada-card-status-critico')
        self.assertContains(response, 'data-mobile-filter="pendentes"')
        self.assertContains(response, 'data-mobile-filter="sugeridos"')
        self.assertContains(response, 'data-mobile-filter="sem_produto"')
        self.assertContains(response, 'data-mobile-filter="lote"')
        self.assertContains(response, 'data-mobile-filter="custo"')
        self.assertContains(response, 'data-mobile-status="todos pendentes lote custo divergencia"')
        self.assertContains(response, 'data-mobile-status="todos pendentes sem_produto"')
        self.assertContains(response, 'data-mobile-status="todos pendentes sugeridos"')
        self.assertContains(response, 'Proxima acao')
        self.assertContains(response, 'Resolver pendencias')
        self.assertContains(response, 'data-mobile-product-form')
        self.assertContains(response, 'produtos-conferencia-mobile')
        conteudo = response.content.decode()
        self.assertLess(
            conteudo.index('data-mobile-priority="10"'),
            conteudo.index('data-mobile-priority="30"'),
        )

    def test_xml_sem_rastro_bloqueia_produto_que_controla_lote_validade(self):
        self.criar_fornecedor()
        produto = self.criar_produto(
            'Produto lote obrigatorio XML',
            controla_lote=True,
            controla_validade=True,
        )
        ProdutoCodigoBarras.objects.create(
            produto=produto,
            ean='7891000000001',
            tipo=ProdutoCodigoBarras.Tipo.FORNECEDOR,
            quantidade_conversao=Decimal('1'),
        )

        entrada = importar_xml_para_entrada(
            self.xml_nfe(self.chave(numero='000000136')),
            filial=self.filial,
            usuario=self.usuario,
        )
        item = entrada.itens.get()

        self.assertEqual(entrada.status, EntradaNF.Status.AGUARDANDO_VINCULOS)
        self.assertEqual(item.diferenca_tipo, 'lote_obrigatorio')
        self.assertTrue(item.diferenca_bloqueante)
        entrada.status = EntradaNF.Status.CONFERIDA
        entrada.save(update_fields=['status', 'updated_at'])
        with self.assertRaisesMessage(DadosInvalidosError, 'lote obrigatorio'):
            CompraService.efetivar_entrada(entrada, self.usuario)

    def test_xml_com_validade_vencida_bloqueia_finalizacao(self):
        self.criar_fornecedor()
        vencida = timezone.localdate() - timedelta(days=1)
        produto = self.criar_produto(
            'Produto validade vencida XML',
            controla_lote=True,
            controla_validade=True,
        )
        ProdutoCodigoBarras.objects.create(
            produto=produto,
            ean='7891000000001',
            tipo=ProdutoCodigoBarras.Tipo.FORNECEDOR,
            quantidade_conversao=Decimal('1'),
        )

        entrada = importar_xml_para_entrada(
            self.xml_nfe(
                self.chave(numero='000000137'),
                rastro_xml=f'''
          <rastro>
            <nLote>XML-VENCIDO</nLote>
            <qLote>2.0000</qLote>
            <dVal>{vencida:%Y-%m-%d}</dVal>
          </rastro>''',
            ),
            filial=self.filial,
            usuario=self.usuario,
        )
        item = entrada.itens.get()

        self.assertEqual(entrada.status, EntradaNF.Status.AGUARDANDO_VINCULOS)
        self.assertEqual(item.diferenca_tipo, 'validade_vencida')
        self.assertTrue(item.diferenca_bloqueante)
        entrada.status = EntradaNF.Status.CONFERIDA
        entrada.save(update_fields=['status', 'updated_at'])
        with self.assertRaisesMessage(DadosInvalidosError, 'validade vencida'):
            CompraService.efetivar_entrada(entrada, self.usuario)

        path = reverse('compras:entrada-finalizacao', args=[entrada.pk])
        request = self.request('get', path)
        response = EntradaNFFinalizacaoView.as_view()(request, pk=entrada.pk)
        self.assertContains(response, 'validade vencida')

    def test_xml_com_validade_proxima_vira_alerta_sem_bloquear_estoque(self):
        self.criar_fornecedor()
        validade = timezone.localdate() + timedelta(days=5)
        produto = self.criar_produto(
            'Produto validade proxima XML',
            controla_lote=True,
            controla_validade=True,
            dias_aviso_vencimento=30,
        )
        ProdutoCodigoBarras.objects.create(
            produto=produto,
            ean='7891000000001',
            tipo=ProdutoCodigoBarras.Tipo.FORNECEDOR,
            quantidade_conversao=Decimal('1'),
        )

        entrada = importar_xml_para_entrada(
            self.xml_nfe(
                self.chave(numero='000000138'),
                rastro_xml=f'''
          <rastro>
            <nLote>XML-PROXIMO</nLote>
            <qLote>2.0000</qLote>
            <dVal>{validade:%Y-%m-%d}</dVal>
          </rastro>''',
            ),
            filial=self.filial,
            usuario=self.usuario,
        )
        item = entrada.itens.get()

        self.assertEqual(entrada.status, EntradaNF.Status.COM_DIFERENCAS)
        self.assertEqual(item.diferenca_tipo, 'validade_proxima')
        self.assertFalse(item.diferenca_bloqueante)
        CompraService.efetivar_entrada(entrada, self.usuario)
        lote = LoteProduto.objects.get(numero_lote='XML-PROXIMO')
        alerta = AlertaVencimento.objects.get(lote=lote, resolvido=False)
        self.assertEqual(alerta.quantidade_em_risco, Decimal('2.000'))
        # A escala virou faixa de dias (d1/d7/d30...); `alto` ficou como
        # legado, para os registros gravados antes. Cinco dias cai em d7.
        self.assertEqual(alerta.nivel_risco, AlertaVencimento.NivelRisco.D7)

    def test_xml_com_multiplos_rastros_separa_itens_por_lote(self):
        self.criar_fornecedor()
        validade = timezone.localdate() + timedelta(days=90)
        produto = self.criar_produto(
            'Produto multi lote XML',
            controla_lote=True,
            controla_validade=True,
        )
        ProdutoCodigoBarras.objects.create(
            produto=produto,
            ean='7891000000001',
            tipo=ProdutoCodigoBarras.Tipo.FORNECEDOR,
            quantidade_conversao=Decimal('1'),
        )

        entrada = importar_xml_para_entrada(
            self.xml_nfe(
                self.chave(numero='000000139'),
                quantidade='3.0000',
                valor_unitario='20.0000',
                valor_produto='60.00',
                rastro_xml=f'''
          <rastro>
            <nLote>XML-LOTE-A</nLote>
            <qLote>1.0000</qLote>
            <dVal>{validade:%Y-%m-%d}</dVal>
          </rastro>
          <rastro>
            <nLote>XML-LOTE-B</nLote>
            <qLote>2.0000</qLote>
            <dVal>{validade:%Y-%m-%d}</dVal>
          </rastro>''',
            ),
            filial=self.filial,
            usuario=self.usuario,
        )

        itens = list(entrada.itens.order_by('numero_lote'))
        self.assertEqual(len(itens), 2)
        self.assertEqual(itens[0].numero_lote, 'XML-LOTE-A')
        self.assertEqual(itens[0].quantidade_xml, Decimal('1.000'))
        self.assertEqual(itens[0].valor_total, Decimal('20.00'))
        self.assertEqual(itens[1].numero_lote, 'XML-LOTE-B')
        self.assertEqual(itens[1].quantidade_xml, Decimal('2.000'))
        self.assertEqual(itens[1].valor_total, Decimal('40.00'))

    def test_finalizacao_bloqueia_item_sem_produto_vinculado(self):
        fornecedor = self.criar_fornecedor(documento='22333444000155')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-SEMVINC',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        entrada.itens.create(
            numero_item=1,
            quantidade=Decimal('1'),
            quantidade_xml=Decimal('1'),
            quantidade_estoque=Decimal('1'),
            quantidade_recebida=Decimal('1'),
            valor_unitario=Decimal('5'),
            valor_bruto=Decimal('5'),
            valor_total=Decimal('5'),
            diferenca_bloqueante=True,
            diferenca_descricao='Produto sem equivalencia interna.',
        )

        with self.assertRaisesMessage(DadosInvalidosError, 'produto sem vinculo'):
            CompraService.efetivar_entrada(entrada, self.usuario)

    def test_listagem_entradas_exibe_pendencias_e_proxima_acao(self):
        fornecedor = self.criar_fornecedor(documento='22333444000156')
        produto = self.criar_produto(
            'Produto painel entrada',
            controla_lote=True,
            controla_validade=True,
        )
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-PAINEL-001',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        item = CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('2'),
            valor_unitario=Decimal('10'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
            numero_lote='LOTE-PENDENTE',
            data_validade=timezone.localdate() + timedelta(days=30),
        )
        item.numero_lote = ''
        item.data_validade = None
        item.save(update_fields=['numero_lote', 'data_validade', 'updated_at'])
        entrada.status = EntradaNF.Status.AGUARDANDO_CONFERENCIA
        entrada.save(update_fields=['status', 'updated_at'])

        path = reverse('compras:entrada-list')
        request = self.request('get', path, {'grupo': 'abertas'})
        response = EntradaNFListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Painel de entradas')
        self.assertContains(response, 'Fila operacional')
        self.assertContains(response, 'Lote pendente')
        self.assertContains(response, 'Preencher lote')
        self.assertContains(response, 'Complete lote e validade obrigatorios.')
        self.assertContains(response, reverse('compras:entrada-conferencia', args=[entrada.pk]))
        self.assertContains(response, 'Fornecedor pendente')
        self.assertContains(response, 'Sem produto')
        self.assertContains(response, 'Custo critico')

    def test_listagem_entradas_filtra_por_produto_pendencia_e_historico(self):
        fornecedor = self.criar_fornecedor(documento='22333444000157')
        produto = self.criar_produto('Produto Busca Operacional')
        entrada_aberta = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-BUSCA-PRODUTO',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        CompraService.adicionar_item_entrada(
            entrada=entrada_aberta,
            produto=produto,
            quantidade=Decimal('1'),
            valor_unitario=Decimal('10'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
        )
        entrada_aberta.status = EntradaNF.Status.CONFERIDA
        entrada_aberta.save(update_fields=['status', 'updated_at'])

        entrada_historico = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-HISTORICO-001',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        CompraService.adicionar_item_entrada(
            entrada=entrada_historico,
            produto=produto,
            quantidade=Decimal('1'),
            valor_unitario=Decimal('10'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
        )
        CompraService.efetivar_entrada(entrada_historico, self.usuario)

        path = reverse('compras:entrada-list')
        request = self.request('get', path, {'q': 'Busca Operacional', 'grupo': 'abertas'})
        response = EntradaNFListView.as_view()(request)

        self.assertContains(response, 'NF-BUSCA-PRODUTO')
        self.assertNotContains(response, 'NF-HISTORICO-001')
        self.assertContains(response, 'Revisar finalizacao')
        self.assertContains(response, reverse('compras:entrada-finalizacao', args=[entrada_aberta.pk]))

        request = self.request('get', path, {'grupo': 'historico'})
        response = EntradaNFListView.as_view()(request)
        self.assertContains(response, 'NF-HISTORICO-001')
        self.assertContains(response, 'Ver resultado')
        self.assertContains(response, reverse('compras:entrada-detail', args=[entrada_historico.pk]))

    def test_tela_localizar_entrada_renderiza_caminhos(self):
        path = reverse('compras:entrada-localizar')
        request = self.request('get', path)
        response = EntradaNFLocalizarNotaView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'XML')
        self.assertContains(response, 'Chave')
        self.assertContains(response, 'Manual')

    def test_usuario_sem_criar_nao_ve_atalhos_de_nova_entrada_e_url_bloqueia(self):
        operador = self.criar_usuario_operador('Operador somente ver', pode_ver=True)

        path = reverse('compras:entrada-localizar')
        request = self.request('get', path)
        request.user = operador
        response = EntradaNFLocalizarNotaView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Você não tem permissão para esta ação.')
        self.assertNotContains(response, reverse('compras:entrada-importar-xml'))
        self.assertNotContains(response, reverse('compras:entrada-consultar-chave'))
        self.assertNotContains(response, reverse('compras:entrada-create'))

        rotas_bloqueadas = [
            (EntradaNFImportarXMLView.as_view(), reverse('compras:entrada-importar-xml'), {}),
            (EntradaNFConsultarChaveView.as_view(), reverse('compras:entrada-consultar-chave'), {}),
            (EntradaNFCreateView.as_view(), reverse('compras:entrada-create'), {}),
        ]
        for view, rota, kwargs in rotas_bloqueadas:
            request = self.request('get', rota)
            request.user = operador
            response = view(request, **kwargs)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, reverse('core:dashboard'))
            self.assertIn('Você não tem permissão para esta ação.', [str(m) for m in request._messages])

    def test_usuario_sem_editar_aprovar_cancelar_bloqueia_acoes_criticas_de_entrada(self):
        operador = self.criar_usuario_operador('Operador bloqueado entrada', pode_ver=True)
        fornecedor = self.criar_fornecedor(documento='22333444000158')
        produto = self.criar_produto('Produto permissao entrada')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-PERM-001',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        item = CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('1'),
            valor_unitario=Decimal('10'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
        )

        rotas_bloqueadas = [
            (AdicionarItemEntradaView.as_view(), reverse('compras:entrada-add-item', args=[entrada.pk]), {'pk': entrada.pk}),
            (EntradaNFVincularItemView.as_view(), reverse('compras:entrada-vincular-item', args=[entrada.pk, item.pk]), {'pk': entrada.pk, 'item_id': item.pk}),
            (EntradaNFCriarProdutoItemView.as_view(), reverse('compras:entrada-criar-produto-item', args=[entrada.pk, item.pk]), {'pk': entrada.pk, 'item_id': item.pk}),
            (EntradaNFReprocessarVinculosView.as_view(), reverse('compras:entrada-reprocessar-vinculos', args=[entrada.pk]), {'pk': entrada.pk}),
            (EntradaNFVincularSugestoesView.as_view(), reverse('compras:entrada-vincular-sugestoes', args=[entrada.pk]), {'pk': entrada.pk}),
            (EntradaNFFornecedorPendenteView.as_view(), reverse('compras:entrada-fornecedor-pendente', args=[entrada.pk]), {'pk': entrada.pk}),
            (EntradaNFCustosView.as_view(), reverse('compras:entrada-custos', args=[entrada.pk]), {'pk': entrada.pk}),
            (EntradaNFGerarContasPagarView.as_view(), reverse('compras:entrada-gerar-contas-pagar', args=[entrada.pk]), {'pk': entrada.pk}),
            (EfetivarEntradaView.as_view(), reverse('compras:entrada-efetivar', args=[entrada.pk]), {'pk': entrada.pk}),
            (CancelarEntradaView.as_view(), reverse('compras:entrada-cancelar', args=[entrada.pk]), {'pk': entrada.pk}),
        ]
        for view, rota, kwargs in rotas_bloqueadas:
            request = self.request('post', rota, {'item_id': str(item.pk)})
            request.user = operador
            response = view(request, **kwargs)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, reverse('core:dashboard'))
            self.assertIn('Você não tem permissão para esta ação.', [str(m) for m in request._messages])

        request = self.request('post', reverse('compras:entrada-diferencas', args=[entrada.pk]), {'item_id': str(item.pk)})
        request.user = operador
        response = EntradaNFDiferencasView.as_view()(request, pk=entrada.pk)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('compras:entrada-diferencas', args=[entrada.pk]))
        self.assertIn('Você não tem permissão para esta ação.', [str(m) for m in request._messages])

        request = self.request('post', reverse('compras:entrada-financeiro', args=[entrada.pk]), {'numero': '001', 'valor': '10.00'})
        request.user = operador
        response = EntradaNFFinanceiroView.as_view()(request, pk=entrada.pk)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('compras:entrada-financeiro', args=[entrada.pk]))
        self.assertIn('Você não tem permissão para esta ação.', [str(m) for m in request._messages])

    def test_view_importar_xml_cria_entrada_e_abre_conferencia(self):
        arquivo = SimpleUploadedFile(
            'nota.xml',
            self.xml_nfe(self.chave(numero='000000126')).encode('utf-8'),
            content_type='text/xml',
        )

        path = reverse('compras:entrada-importar-xml')
        request = self.request('post', path, {'arquivo_xml': arquivo})
        response = EntradaNFImportarXMLView.as_view()(request)

        entrada = EntradaNF.objects.get(numero_nf='123')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('compras:entrada-conferencia', args=[entrada.pk]))

        request = self.request('get', response.url)
        conferencia = EntradaNFConferenciaView.as_view()(request, pk=entrada.pk)
        self.assertEqual(conferencia.status_code, 200)
        self.assertContains(conferencia, 'Produto de fornecedor')

    def test_view_importar_xml_duplicado_redireciona_para_entrada_existente(self):
        chave = self.chave(numero='000000128')
        entrada = importar_xml_para_entrada(
            self.xml_nfe(chave),
            filial=self.filial,
            usuario=self.usuario,
        )
        arquivo = SimpleUploadedFile(
            'nota_duplicada.xml',
            self.xml_nfe(chave).encode('utf-8'),
            content_type='text/xml',
        )

        request = self.request('post', reverse('compras:entrada-importar-xml'), {'arquivo_xml': arquivo})
        response = EntradaNFImportarXMLView.as_view()(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('compras:entrada-detail', args=[entrada.pk])}?duplicada=xml")
        self.assertIn('Esta nota ja existe nesta filial', [str(m) for m in request._messages][0])

    def test_detail_entrada_duplicada_exibe_acoes_operacionais(self):
        entrada = importar_xml_para_entrada(
            self.xml_nfe(self.chave(numero='000000129')),
            filial=self.filial,
            usuario=self.usuario,
        )

        request = self.request('get', f"{reverse('compras:entrada-detail', args=[entrada.pk])}?duplicada=xml")
        response = EntradaNFDetailView.as_view()(request, pk=entrada.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Esta NF ja foi importada nesta filial')
        self.assertContains(response, 'O que voce pode fazer agora')
        self.assertContains(response, 'Cancelar entrada anterior')
        self.assertNotContains(response, 'Ver na lista')
        self.assertNotContains(response, 'Solicitar estorno')
        self.assertContains(response, reverse('compras:entrada-conferencia', args=[entrada.pk]))
        self.assertContains(response, reverse('compras:entrada-cancelar', args=[entrada.pk]))

    def test_detail_destinatario_diferente_exibe_cnpj_da_nota(self):
        xml = self.xml_nfe(
            self.chave(numero='000000180'),
            dest_doc='12345678901',
        )
        entrada = importar_xml_para_entrada(xml, filial=self.filial, usuario=self.usuario)

        request = self.request('get', reverse('compras:entrada-detail', args=[entrada.pk]))
        response = EntradaNFDetailView.as_view()(request, pk=entrada.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Atencao, essa nota nao possui o mesmo CNPJ que o cadastrado na filial.')
        self.assertContains(response, 'Essa nota esta vinculada ao CNPJ:')
        self.assertContains(response, '12345678901')

    def test_remover_item_entrada_aberta_remove_e_audita(self):
        produto = self.criar_produto('Produto remover entrada')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            fornecedor=self.criar_fornecedor(),
            numero_nf='REM-1',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
            usuario=self.usuario,
        )
        item = CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('2'),
            valor_unitario=Decimal('10'),
        )

        request = self.request('post', reverse('compras:entrada-del-item', args=[entrada.pk, item.pk]))
        response = RemoverItemEntradaView.as_view()(request, pk=entrada.pk, item_id=item.pk)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(entrada.itens.filter(pk=item.pk).exists())
        log = RegistroAuditoria.objects.get(acao='remover_item')
        self.assertEqual(log.objeto_id, entrada.pk)
        self.assertEqual(log.metadados['item_removido']['id'], item.pk)

    def test_remover_item_entrada_efetivada_bloqueia(self):
        produto = self.criar_produto('Produto remover bloqueado')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            fornecedor=self.criar_fornecedor(),
            numero_nf='REM-2',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
            usuario=self.usuario,
        )
        item = CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('2'),
            valor_unitario=Decimal('10'),
        )
        entrada.status = EntradaNF.Status.EFETIVADA
        entrada.save(update_fields=['status'])

        request = self.request('post', reverse('compras:entrada-del-item', args=[entrada.pk, item.pk]))
        response = RemoverItemEntradaView.as_view()(request, pk=entrada.pk, item_id=item.pk)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(entrada.itens.filter(pk=item.pk).exists())

    def test_conferencia_entrada_de_outra_filial_redireciona_para_lista(self):
        outra_filial = Filial.objects.create(
            empresa=self.empresa,
            razao_social='Outra Filial Entrada',
            nome_fantasia='Outra Entrada',
            cnpj='41234567000193',
            uf='RN',
        )
        entrada = importar_xml_para_entrada(
            self.xml_nfe(self.chave(numero='000000130')),
            filial=self.filial,
            usuario=self.usuario,
        )

        request = self.request('get', reverse('compras:entrada-conferencia', args=[entrada.pk]))
        request.filial_ativa = outra_filial
        response = EntradaNFConferenciaView.as_view()(request, pk=entrada.pk)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('compras:entrada-list')}?fora_filial=1")
        self.assertIn('Esta entrada pertence a outra filial', [str(m) for m in request._messages][0])

    def test_view_importar_xml_exibe_dropzone_na_area_inteira(self):
        request = self.request('get', reverse('compras:entrada-importar-xml'))

        response = EntradaNFImportarXMLView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-xml-dropzone')
        self.assertContains(response, 'Arraste o XML para qualquer ponto desta area')
        self.assertContains(response, 'data-xml-file-name')

    def test_conferencia_sugere_produtos_por_nome_e_ncm(self):
        self.criar_produto(descricao='Produto fornecedor estoque interno')
        entrada = importar_xml_para_entrada(
            self.xml_nfe(self.chave(numero='000000127')),
            filial=self.filial,
            usuario=self.usuario,
        )

        path = reverse('compras:entrada-conferencia', args=[entrada.pk])
        request = self.request('get', path)
        response = EntradaNFConferenciaView.as_view()(request, pk=entrada.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sugestoes')
        self.assertContains(response, 'Produto fornecedor estoque interno')
        item = entrada.itens.get()
        self.assertContains(response, f'name="produto_{item.pk}"')
        self.assertContains(response, f'name="fator_conversao_{item.pk}"')
        self.assertContains(response, 'Reprocessar vinculos')
        self.assertContains(response, 'Custos')
        self.assertContains(response, 'Financeiro')
        self.assertContains(response, 'Cadastrar pelo XML')

    def test_conferencia_reprocessa_vinculo_por_ean_cadastrado_apos_xml(self):
        entrada = importar_xml_para_entrada(
            self.xml_nfe(self.chave(numero='000000141')),
            filial=self.filial,
            usuario=self.usuario,
        )
        item = entrada.itens.get()
        self.assertIsNone(item.produto)

        produto = self.criar_produto(descricao='Produto cadastrado depois da nota')
        ProdutoCodigoBarras.objects.create(
            produto=produto,
            ean='7891000000001',
            tipo=ProdutoCodigoBarras.Tipo.FORNECEDOR,
            quantidade_conversao=Decimal('12'),
        )

        path = reverse('compras:entrada-reprocessar-vinculos', args=[entrada.pk])
        request = self.request('post', path)
        response = EntradaNFReprocessarVinculosView.as_view()(request, pk=entrada.pk)

        item.refresh_from_db()
        entrada.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(item.produto, produto)
        self.assertEqual(item.fator_conversao, Decimal('12.0000'))
        self.assertEqual(item.quantidade_recebida, Decimal('24.000'))
        self.assertEqual(item.valor_bruto, Decimal('60.00'))
        self.assertEqual(item.valor_unitario, Decimal('2.5000'))
        self.assertEqual(entrada.status, EntradaNF.Status.AGUARDANDO_CONFERENCIA)
        equivalencia = ProdutoFornecedorEquivalencia.objects.get(
            produto=produto,
            fornecedor=entrada.fornecedor,
            codigo_fornecedor='FORN-001',
        )
        self.assertEqual(equivalencia.origem, ProdutoFornecedorEquivalencia.Origem.XML)

    def test_conferencia_confirma_sugestoes_em_lote(self):
        produto = self.criar_produto(descricao='Produto fornecedor estoque interno')
        entrada = importar_xml_para_entrada(
            self.xml_nfe(self.chave(numero='000000132')),
            filial=self.filial,
            usuario=self.usuario,
        )
        item = entrada.itens.get()

        path = reverse('compras:entrada-vincular-sugestoes', args=[entrada.pk])
        request = self.request('post', path, {
            'item': [str(item.pk)],
            f'produto_{item.pk}': str(produto.pk),
            f'fator_conversao_{item.pk}': '3',
            f'unidade_estoque_{item.pk}': 'UN',
            f'numero_lote_{item.pk}': 'LOTE-132',
            f'data_validade_{item.pk}': '2026-12-31',
        })
        response = EntradaNFVincularSugestoesView.as_view()(request, pk=entrada.pk)

        item.refresh_from_db()
        entrada.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(item.produto, produto)
        self.assertEqual(item.fator_conversao, Decimal('3'))
        self.assertEqual(item.quantidade_recebida, Decimal('6.0000'))
        self.assertEqual(item.valor_bruto, Decimal('60.00'))
        self.assertEqual(item.valor_unitario, Decimal('10.0000'))
        self.assertEqual(item.unidade_estoque, 'UN')
        self.assertEqual(item.numero_lote, 'LOTE-132')
        self.assertEqual(item.data_validade, date(2026, 12, 31))
        self.assertEqual(entrada.status, EntradaNF.Status.AGUARDANDO_CONFERENCIA)
        self.assertTrue(
            ProdutoFornecedorEquivalencia.objects.filter(
                produto=produto,
                fornecedor=entrada.fornecedor,
                codigo_fornecedor='FORN-001',
            ).exists()
        )

    def test_conferencia_lote_aceita_segunda_sugestao_recalculada(self):
        self.criar_produto(descricao='Produto fornecedor estoque interno')
        produto_alternativo = self.criar_produto(descricao='Produto fornecedor alternativa interna')
        entrada = importar_xml_para_entrada(
            self.xml_nfe(self.chave(numero='000000137')),
            filial=self.filial,
            usuario=self.usuario,
        )
        item = entrada.itens.get()

        path = reverse('compras:entrada-vincular-sugestoes', args=[entrada.pk])
        request = self.request('post', path, {
            'item': [str(item.pk)],
            f'produto_{item.pk}': str(produto_alternativo.pk),
            f'fator_conversao_{item.pk}': '1',
            f'unidade_estoque_{item.pk}': 'UN',
        })
        response = EntradaNFVincularSugestoesView.as_view()(request, pk=entrada.pk)

        item.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(item.produto, produto_alternativo)

    def test_conferencia_lote_ignora_sugestao_trocada(self):
        produto_sugerido = self.criar_produto(descricao='Produto fornecedor estoque interno')
        produto_trocado = self.criar_produto(descricao='Outro produto interno')
        produto_trocado.descricao = 'Sem relacao operacional'
        produto_trocado.ncm = '00000000'
        produto_trocado.save()
        entrada = importar_xml_para_entrada(
            self.xml_nfe(self.chave(numero='000000133')),
            filial=self.filial,
            usuario=self.usuario,
        )
        item = entrada.itens.get()

        path = reverse('compras:entrada-vincular-sugestoes', args=[entrada.pk])
        request = self.request('post', path, {
            'item': [str(item.pk)],
            f'produto_{item.pk}': str(produto_trocado.pk),
            f'fator_conversao_{item.pk}': '1',
            f'unidade_estoque_{item.pk}': 'UN',
        })
        response = EntradaNFVincularSugestoesView.as_view()(request, pk=entrada.pk)

        item.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(item.produto)
        self.assertFalse(
            ProdutoFornecedorEquivalencia.objects.filter(produto=produto_sugerido).exists()
        )

    def test_criar_produto_pelo_item_xml_cadastra_e_vincula_item(self):
        entrada = importar_xml_para_entrada(
            self.xml_nfe(self.chave(numero='000000128')),
            filial=self.filial,
            usuario=self.usuario,
        )
        item = entrada.itens.get()

        path = reverse('compras:entrada-criar-produto-item', args=[entrada.pk, item.pk])
        request = self.request('post', path)
        response = EntradaNFCriarProdutoItemView.as_view()(request, pk=entrada.pk, item_id=item.pk)

        item.refresh_from_db()
        produto = item.produto
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(produto)
        self.assertEqual(produto.descricao, 'Produto de fornecedor')
        self.assertEqual(produto.ncm, '20089900')
        self.assertEqual(produto.codigo_barras, '7891000000001')
        self.assertFalse(produto.permite_venda_sem_estoque)
        self.assertTrue(ProdutoFilial.objects.filter(produto=produto, filial=self.filial).exists())
        self.assertTrue(ProdutoCodigoBarras.objects.filter(produto=produto, ean='7891000000001').exists())
        self.assertTrue(
            ProdutoFornecedorEquivalencia.objects.filter(
                produto=produto,
                fornecedor=entrada.fornecedor,
                codigo_fornecedor='FORN-001',
            ).exists()
        )

    def test_criar_produto_pelo_xml_com_rastro_habilita_lote_validade(self):
        validade = timezone.localdate() + timedelta(days=90)
        fabricacao = timezone.localdate() - timedelta(days=5)
        entrada = importar_xml_para_entrada(
            self.xml_nfe(
                self.chave(numero='000000140'),
                rastro_xml=f'''
          <rastro>
            <nLote>XML-CAD-LOTE</nLote>
            <qLote>2.0000</qLote>
            <dFab>{fabricacao:%Y-%m-%d}</dFab>
            <dVal>{validade:%Y-%m-%d}</dVal>
          </rastro>''',
            ),
            filial=self.filial,
            usuario=self.usuario,
        )
        item = entrada.itens.get()

        path = reverse('compras:entrada-criar-produto-item', args=[entrada.pk, item.pk])
        request = self.request('post', path)
        response = EntradaNFCriarProdutoItemView.as_view()(request, pk=entrada.pk, item_id=item.pk)

        item.refresh_from_db()
        entrada.refresh_from_db()
        produto = item.produto
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(produto)
        self.assertTrue(produto.controla_lote)
        self.assertTrue(produto.controla_validade)
        self.assertEqual(item.numero_lote, 'XML-CAD-LOTE')
        self.assertEqual(item.data_fabricacao, fabricacao)
        self.assertEqual(item.data_validade, validade)
        self.assertEqual(entrada.status, EntradaNF.Status.AGUARDANDO_CONFERENCIA)

        CompraService.efetivar_entrada(entrada, self.usuario)
        self.assertTrue(
            LoteProduto.objects.filter(
                produto=produto,
                filial=self.filial,
                numero_lote='XML-CAD-LOTE',
            ).exists()
        )

    def test_criar_produto_pelo_xml_reaproveita_produto_de_outro_lote(self):
        validade = timezone.localdate() + timedelta(days=90)
        entrada = importar_xml_para_entrada(
            self.xml_nfe(
                self.chave(numero='000000141'),
                quantidade='3.0000',
                valor_unitario='20.0000',
                valor_produto='60.00',
                rastro_xml=f'''
          <rastro>
            <nLote>XML-CAD-A</nLote>
            <qLote>1.0000</qLote>
            <dVal>{validade:%Y-%m-%d}</dVal>
          </rastro>
          <rastro>
            <nLote>XML-CAD-B</nLote>
            <qLote>2.0000</qLote>
            <dVal>{validade:%Y-%m-%d}</dVal>
          </rastro>''',
            ),
            filial=self.filial,
            usuario=self.usuario,
        )
        item_a, item_b = list(entrada.itens.order_by('numero_lote'))

        path_a = reverse('compras:entrada-criar-produto-item', args=[entrada.pk, item_a.pk])
        request_a = self.request('post', path_a)
        EntradaNFCriarProdutoItemView.as_view()(request_a, pk=entrada.pk, item_id=item_a.pk)
        item_a.refresh_from_db()
        produto = item_a.produto

        path_b = reverse('compras:entrada-criar-produto-item', args=[entrada.pk, item_b.pk])
        request_b = self.request('post', path_b)
        response = EntradaNFCriarProdutoItemView.as_view()(request_b, pk=entrada.pk, item_id=item_b.pk)

        item_b.refresh_from_db()
        entrada.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(item_b.produto, produto)
        self.assertEqual(
            Produto.objects.filter(filial=self.filial, codigo_barras='7891000000001').count(),
            1,
        )
        self.assertEqual(entrada.status, EntradaNF.Status.AGUARDANDO_CONFERENCIA)

    def test_vincular_item_aceita_fator_decimal_localizado_da_tela(self):
        produto = self.criar_produto('Produto interno localizado')
        entrada = importar_xml_para_entrada(
            self.xml_nfe(self.chave(numero='000000129')),
            filial=self.filial,
            usuario=self.usuario,
        )
        item = entrada.itens.get()

        path = reverse('compras:entrada-vincular-item', args=[entrada.pk, item.pk])
        request = self.request('post', path, {
            'produto': str(produto.pk),
            'fator_conversao': '1,0000',
            'unidade_estoque': 'UN',
        })
        response = EntradaNFVincularItemView.as_view()(request, pk=entrada.pk, item_id=item.pk)

        item.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(item.produto, produto)
        self.assertEqual(item.fator_conversao, Decimal('1.0000'))
        self.assertTrue(
            ProdutoFornecedorEquivalencia.objects.filter(
                produto=produto,
                fornecedor=entrada.fornecedor,
                codigo_fornecedor='FORN-001',
            ).exists()
        )

    def test_xml_importa_duplicatas_como_pre_financeiro(self):
        cobr_xml = '''
      <cobr>
        <fat>
          <nFat>123</nFat>
          <vOrig>60.00</vOrig>
          <vLiq>60.00</vLiq>
        </fat>
        <dup>
          <nDup>001</nDup>
          <dVenc>2026-06-20</dVenc>
          <vDup>30.00</vDup>
        </dup>
        <dup>
          <nDup>002</nDup>
          <dVenc>2026-07-20</dVenc>
          <vDup>30.00</vDup>
        </dup>
      </cobr>'''
        entrada = importar_xml_para_entrada(
            self.xml_nfe(self.chave(numero='000000130'), cobr_xml=cobr_xml),
            filial=self.filial,
            usuario=self.usuario,
        )

        parcelas = list(entrada.parcelas_financeiras.order_by('numero'))
        self.assertEqual(len(parcelas), 2)
        self.assertEqual(parcelas[0].numero, '001')
        self.assertEqual(parcelas[0].valor, Decimal('30.00'))
        self.assertEqual(parcelas[0].origem, EntradaNFParcela.Origem.XML)
        self.assertEqual(parcelas[0].status, EntradaNFParcela.Status.PENDENTE)
        self.assertEqual(parcelas[0].emitente_documento_xml, '11222333000144')

    def test_financeiro_permite_parcela_manual_sem_gerar_conta_pagar(self):
        entrada = importar_xml_para_entrada(
            self.xml_nfe(self.chave(numero='000000131')),
            filial=self.filial,
            usuario=self.usuario,
        )

        path = reverse('compras:entrada-financeiro', args=[entrada.pk])
        request = self.request('post', path, {
            'numero': '',
            'data_vencimento': '2026-06-25',
            'valor': '60.00',
            'forma_pagamento': 'Boleto',
            'observacao': 'Parcela manual',
        })
        response = EntradaNFFinanceiroView.as_view()(request, pk=entrada.pk)

        parcela = entrada.parcelas_financeiras.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parcela.numero, '001')
        self.assertEqual(parcela.origem, EntradaNFParcela.Origem.MANUAL)
        self.assertEqual(parcela.conta_pagar_id, None)

        request = self.request('get', path)
        tela = EntradaNFFinanceiroView.as_view()(request, pk=entrada.pk)
        self.assertEqual(tela.status_code, 200)
        self.assertContains(tela, 'Boleto')
        self.assertContains(tela, 'Revise as parcelas')

    def test_financeiro_gera_conta_pagar_apenas_apos_entrada_efetivada(self):
        fornecedor = self.criar_fornecedor(documento='66777888000199')
        produto = self.criar_produto('Produto financeiro entrada')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-FIN-GERAR',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('2'),
            valor_unitario=Decimal('30'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
        )
        EntradaNFParcela.objects.create(
            entrada=entrada,
            numero='001',
            data_vencimento=timezone.localdate(),
            valor=Decimal('60.00'),
            forma_pagamento='Boleto',
            origem=EntradaNFParcela.Origem.MANUAL,
        )

        path = reverse('compras:entrada-gerar-contas-pagar', args=[entrada.pk])
        request = self.request('post', path)
        response = EntradaNFGerarContasPagarView.as_view()(request, pk=entrada.pk)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ContaPagar.objects.exists())

        CompraService.efetivar_entrada(entrada, self.usuario)
        request = self.request('post', path)
        response = EntradaNFGerarContasPagarView.as_view()(request, pk=entrada.pk)

        parcela = entrada.parcelas_financeiras.get()
        conta = ContaPagar.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(conta.filial, self.filial)
        self.assertEqual(conta.fornecedor, fornecedor)
        self.assertEqual(conta.documento_tipo, 'entrada_nf')
        self.assertEqual(conta.documento_id, entrada.pk)
        self.assertEqual(conta.documento_numero, 'NF-FIN-GERAR')
        self.assertEqual(conta.nota_fiscal_fornecedor, 'NF-FIN-GERAR')
        self.assertEqual(conta.parcela, 1)
        self.assertEqual(conta.total_parcelas, 1)
        self.assertEqual(conta.valor_original, Decimal('60.00'))
        self.assertEqual(conta.valor_final, Decimal('60.00'))
        self.assertEqual(conta.valor_saldo, Decimal('60.00'))
        self.assertEqual(conta.status, StatusContaPagar.ABERTO)
        self.assertEqual(parcela.status, EntradaNFParcela.Status.GERADA)
        self.assertEqual(parcela.conta_pagar_id, conta.pk)

        request = self.request('post', path)
        EntradaNFGerarContasPagarView.as_view()(request, pk=entrada.pk)
        self.assertEqual(ContaPagar.objects.count(), 1)

    def test_financeiro_nao_gera_conta_pagar_com_fornecedor_pendente(self):
        entrada = importar_xml_para_entrada(
            self.xml_nfe(
                self.chave(numero='000000134', cnpj='12312312000155'),
                emit_doc='12312312000155',
                cobr_xml='''
      <cobr>
        <dup>
          <nDup>001</nDup>
          <dVenc>2026-06-20</dVenc>
          <vDup>60.00</vDup>
        </dup>
      </cobr>''',
            ),
            filial=self.filial,
            usuario=self.usuario,
        )
        entrada.fornecedor_pendente = True
        entrada.status = EntradaNF.Status.EFETIVADA
        entrada.save(update_fields=['fornecedor_pendente', 'status', 'updated_at'])

        path = reverse('compras:entrada-gerar-contas-pagar', args=[entrada.pk])
        request = self.request('post', path)
        response = EntradaNFGerarContasPagarView.as_view()(request, pk=entrada.pk)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ContaPagar.objects.exists())
        self.assertEqual(entrada.parcelas_financeiras.get().status, EntradaNFParcela.Status.PENDENTE)

    def test_tela_financeiro_exibe_acao_de_geracao_para_entrada_efetivada(self):
        fornecedor = self.criar_fornecedor(documento='77888999000100')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-FIN-TELA',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        entrada.valor_total = Decimal('25.00')
        entrada.status = EntradaNF.Status.EFETIVADA
        entrada.save(update_fields=['valor_total', 'status', 'updated_at'])
        EntradaNFParcela.objects.create(
            entrada=entrada,
            numero='001',
            data_vencimento=timezone.localdate(),
            valor=Decimal('25.00'),
            origem=EntradaNFParcela.Origem.MANUAL,
        )

        path = reverse('compras:entrada-financeiro', args=[entrada.pk])
        request = self.request('get', path)
        response = EntradaNFFinanceiroView.as_view()(request, pk=entrada.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gerar contas a pagar')
        self.assertContains(response, '1 parcela(s) pronta(s) para gerar')

    def test_diferencas_justifica_quantidade_recebida_e_libera_bloqueio(self):
        fornecedor = self.criar_fornecedor(documento='33444555000166')
        produto = self.criar_produto('Produto com recebimento parcial')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-DIFF-QTD',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        item = CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('2'),
            valor_unitario=Decimal('10'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
        )

        path = reverse('compras:entrada-diferencas', args=[entrada.pk])
        request = self.request('post', path, {
            'item_id': str(item.pk),
            'quantidade_recebida': '1,000',
            'numero_lote': '',
            'data_validade': '',
            'justificativa_diferenca': 'Recebimento parcial conferido na doca.',
        })
        response = EntradaNFDiferencasView.as_view()(request, pk=entrada.pk)

        item.refresh_from_db()
        entrada.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(item.quantidade_recebida, Decimal('1.000'))
        self.assertEqual(item.diferenca_tipo, 'quantidade_recebida')
        self.assertFalse(item.diferenca_bloqueante)
        self.assertEqual(entrada.status, EntradaNF.Status.COM_DIFERENCAS)
        CompraService._validar_itens_para_efetivar(entrada)

    def test_diferencas_lote_obrigatorio_permanece_bloqueante_ate_informar_lote(self):
        fornecedor = self.criar_fornecedor(documento='44555666000177')
        produto = self.criar_produto('Produto rastreado')
        produto.controla_lote = True
        produto.save(update_fields=['controla_lote', 'updated_at'])
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-DIFF-LOTE',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        item = entrada.itens.create(
            produto=produto,
            numero_item=1,
            quantidade=Decimal('2'),
            quantidade_xml=Decimal('2'),
            quantidade_estoque=Decimal('2'),
            quantidade_recebida=Decimal('2'),
            unidade_xml='UN',
            unidade_estoque='UN',
            fator_conversao=Decimal('1'),
            valor_unitario=Decimal('10'),
            valor_bruto=Decimal('20'),
            valor_total=Decimal('20'),
        )

        path = reverse('compras:entrada-diferencas', args=[entrada.pk])
        request = self.request('post', path, {
            'item_id': str(item.pk),
            'quantidade_recebida': '2,000',
            'numero_lote': '',
            'data_validade': '',
            'justificativa_diferenca': '',
        })
        response = EntradaNFDiferencasView.as_view()(request, pk=entrada.pk)
        item.refresh_from_db()
        entrada.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(item.diferenca_tipo, 'lote_obrigatorio')
        self.assertTrue(item.diferenca_bloqueante)
        self.assertEqual(entrada.status, EntradaNF.Status.AGUARDANDO_VINCULOS)

        request = self.request('post', path, {
            'item_id': str(item.pk),
            'quantidade_recebida': '2,000',
            'numero_lote': 'LOTE-01',
            'data_validade': '',
            'justificativa_diferenca': '',
        })
        EntradaNFDiferencasView.as_view()(request, pk=entrada.pk)
        item.refresh_from_db()
        entrada.refresh_from_db()
        self.assertEqual(item.numero_lote, 'LOTE-01')
        self.assertEqual(item.diferenca_tipo, '')
        self.assertFalse(item.diferenca_bloqueante)
        self.assertEqual(entrada.status, EntradaNF.Status.AGUARDANDO_CONFERENCIA)

    def test_tela_diferencas_renderiza_edicao_da_conferencia(self):
        fornecedor = self.criar_fornecedor(documento='55666777000188')
        produto = self.criar_produto('Produto com diferenca visual')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-DIFF-TELA',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        item = CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('3'),
            valor_unitario=Decimal('10'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
        )
        item.quantidade_recebida = Decimal('2')
        item.diferenca_tipo = 'quantidade_recebida'
        item.diferenca_descricao = 'Quantidade recebida diferente da nota.'
        item.diferenca_bloqueante = True
        item.save(update_fields=[
            'quantidade_recebida', 'diferenca_tipo', 'diferenca_descricao',
            'diferenca_bloqueante', 'updated_at',
        ])

        path = reverse('compras:entrada-diferencas', args=[entrada.pk])
        request = self.request('get', path)
        response = EntradaNFDiferencasView.as_view()(request, pk=entrada.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Quantidade recebida')
        self.assertContains(response, 'Justificativa')
        self.assertContains(response, 'Salvar diferenca')

    def test_tela_diferencas_recalcula_lote_obrigatorio_para_exibicao(self):
        fornecedor = self.criar_fornecedor(documento='66777888000199')
        produto = self.criar_produto('Produto rastreio visual')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-DIFF-STALE',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        item = CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('2'),
            valor_unitario=Decimal('10'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
        )
        produto.controla_lote = True
        produto.save(update_fields=['controla_lote', 'updated_at'])
        item.diferenca_tipo = ''
        item.diferenca_descricao = ''
        item.diferenca_bloqueante = False
        item.save(update_fields=[
            'diferenca_tipo', 'diferenca_descricao', 'diferenca_bloqueante', 'updated_at',
        ])

        path = reverse('compras:entrada-diferencas', args=[entrada.pk])
        request = self.request('get', path)
        response = EntradaNFDiferencasView.as_view()(request, pk=entrada.pk)

        item.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Produto exige lote para movimentar estoque')
        self.assertContains(response, 'lote obrigatorio')
        self.assertEqual(item.diferenca_tipo, '')

    def test_finalizacao_recalcula_bloqueios_antes_de_liberar_botao(self):
        fornecedor = self.criar_fornecedor(documento='77888999000100')
        produto = self.criar_produto('Produto finalizacao rastreavel')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-FINAL-STALE',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        item = CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('2'),
            valor_unitario=Decimal('10'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
        )
        produto.controla_lote = True
        produto.save(update_fields=['controla_lote', 'updated_at'])
        item.diferenca_tipo = ''
        item.diferenca_descricao = ''
        item.diferenca_bloqueante = False
        item.save(update_fields=[
            'diferenca_tipo', 'diferenca_descricao', 'diferenca_bloqueante', 'updated_at',
        ])

        path = reverse('compras:entrada-finalizacao', args=[entrada.pk])
        request = self.request('get', path)
        response = EntradaNFFinalizacaoView.as_view()(request, pk=entrada.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1 diferenca(s) bloqueante(s) pendente(s)')
        self.assertContains(response, '1 item(ns) com lote obrigatorio pendente')
        self.assertContains(response, 'Resolver diferencas')
        self.assertNotContains(response, reverse('compras:entrada-efetivar', args=[entrada.pk]))

    def test_efetivacao_ignora_item_recusado_por_validade_vencida(self):
        fornecedor = self.criar_fornecedor(documento='77888999000101')
        produto_ok = self.criar_produto(
            'Produto recebido com validade',
            controla_lote=True,
            controla_validade=True,
        )
        produto_vencido = self.criar_produto(
            'Produto recusado vencido',
            controla_lote=True,
            controla_validade=True,
        )
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-RECUSA-VENCIDO',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto_ok,
            quantidade=Decimal('3'),
            valor_unitario=Decimal('10'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
            numero_lote='LOTE-OK',
            data_validade=timezone.localdate() + timedelta(days=90),
        )
        item_vencido = CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto_vencido,
            quantidade=Decimal('2'),
            valor_unitario=Decimal('20'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
            numero_lote='LOTE-VENCIDO',
            data_validade=timezone.localdate() + timedelta(days=30),
        )
        item_vencido.quantidade_recebida = Decimal('0')
        item_vencido.data_validade = timezone.localdate() - timedelta(days=1)
        item_vencido.justificativa_diferenca = 'Item vencido recusado no recebimento.'
        CompraService.atualizar_diferenca_item(item_vencido)

        path = reverse('compras:entrada-finalizacao', args=[entrada.pk])
        request = self.request('get', path)
        response = EntradaNFFinalizacaoView.as_view()(request, pk=entrada.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('compras:entrada-efetivar', args=[entrada.pk]))
        self.assertContains(response, 'Recusado na conferencia')
        self.assertContains(response, 'Nao movimenta')
        self.assertNotContains(response, 'item(ns) com validade vencida')

        CompraService.efetivar_entrada(entrada, self.usuario)

        entrada.refresh_from_db()
        item_vencido.refresh_from_db()
        self.assertEqual(entrada.status, EntradaNF.Status.EFETIVADA)
        self.assertEqual(item_vencido.quantidade_recebida, Decimal('0'))
        self.assertIsNone(item_vencido.lote_gerado_id)
        self.assertFalse(
            MovimentacaoEstoque.objects.filter(
                produto=produto_vencido,
                documento_id=entrada.pk,
            ).exists()
        )
        self.assertTrue(
            MovimentacaoEstoque.objects.filter(
                produto=produto_ok,
                documento_id=entrada.pk,
            ).exists()
        )

        path = reverse('compras:entrada-detail', args=[entrada.pk])
        request = self.request('get', path)
        response = EntradaNFDetailView.as_view()(request, pk=entrada.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Recusado na conferencia')
        self.assertContains(response, 'Nao movimenta')
        self.assertContains(response, 'Item vencido recusado no recebimento.')

    def test_detalhe_pos_efetivacao_exibe_resultado_links_custos_e_recusas(self):
        fornecedor = self.criar_fornecedor(documento='77888999000102')
        produto_ok = self.criar_produto(
            'Produto pos efetivacao',
            controla_lote=True,
            controla_validade=True,
        )
        produto_recusado = self.criar_produto(
            'Produto recusado auditavel',
            controla_lote=True,
            controla_validade=True,
        )
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-POS-EFET',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        item_ok = CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto_ok,
            quantidade=Decimal('3'),
            valor_unitario=Decimal('10'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
            numero_lote='LOTE-POS-OK',
            data_validade=timezone.localdate() + timedelta(days=90),
        )
        item_recusado = CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto_recusado,
            quantidade=Decimal('2'),
            valor_unitario=Decimal('20'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
            numero_lote='LOTE-POS-REC',
            data_validade=timezone.localdate() + timedelta(days=30),
        )
        item_recusado.quantidade_recebida = Decimal('0')
        item_recusado.data_validade = timezone.localdate() - timedelta(days=1)
        item_recusado.justificativa_diferenca = 'Item recusado para auditoria.'
        CompraService.atualizar_diferenca_item(item_recusado)

        request_post = self.request(
            'post',
            reverse('compras:entrada-efetivar', args=[entrada.pk]),
            {'confirmar_resumo_final': '1'},
        )
        response = EfetivarEntradaView.as_view()(request_post, pk=entrada.pk)
        self.assertEqual(response.status_code, 302)
        mensagens = [str(message) for message in request_post._messages]
        self.assertIn('Entrada efetivada: 1 produto(s), 3 unidade(s), R$ 30,00 custo total', mensagens)

        entrada.refresh_from_db()
        item_ok.refresh_from_db()
        item_recusado.refresh_from_db()
        estoque = Estoque.objects.get(produto=produto_ok, filial=self.filial)
        self.assertEqual(estoque.custo_medio, item_ok.custo_unitario_total)
        self.assertIsNotNone(item_ok.lote_gerado_id)
        self.assertIsNone(item_recusado.lote_gerado_id)

        request_get = self.request('get', reverse('compras:entrada-detail', args=[entrada.pk]))
        response = EntradaNFDetailView.as_view()(request_get, pk=entrada.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Resultado da efetivacao')
        self.assertContains(response, 'Movimentos de estoque gerados')
        self.assertContains(response, 'Lotes gerados')
        self.assertContains(response, 'Custos gravados')
        self.assertContains(response, 'Itens recusados / nao movimentados')
        self.assertContains(response, f'documento_id={entrada.pk}')
        self.assertContains(response, reverse('estoque:lote-update', args=[item_ok.lote_gerado_id]))
        self.assertContains(response, 'Ver extrato do produto')
        self.assertContains(response, 'Item recusado para auditoria.')
        self.assertContains(response, 'R$ 10,00')
        self.assertContains(response, 'R$ 30,00')

    def test_produto_criado_pelo_xml_nasce_rascunho_comercial_e_aparece_na_entrada(self):
        fornecedor = self.criar_fornecedor(documento='77888999000120')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-RASCUNHO-COM',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
            origem_entrada=EntradaNF.OrigemEntrada.XML,
        )
        item = CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=None,
            quantidade=Decimal('4'),
            valor_unitario=Decimal('12.50'),
            unidade_xml='UN',
            unidade_estoque='UN',
            fator_conversao=Decimal('1'),
            ean_xml='7891234567890',
            codigo_produto_fornecedor='XML-RASC',
            descricao_xml='Produto novo XML incompleto',
        )

        produto = criar_produto_e_vincular_item(entrada, item)
        entrada.refresh_from_db()
        entrada.status = EntradaNF.Status.CONFERIDA
        entrada.save(update_fields=['status', 'updated_at'])
        CompraService.efetivar_entrada(entrada, self.usuario)

        produto.refresh_from_db()
        self.assertTrue(produto.rascunho_comercial)
        self.assertFalse(contrato_pdv_produto(produto, filial=self.filial)['pode_vender'])

        request_get = self.request('get', reverse('compras:entrada-detail', args=[entrada.pk]))
        response = EntradaNFDetailView.as_view()(request_get, pk=entrada.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Prontidao comercial dos produtos recebidos')
        self.assertContains(response, 'Produto criado pelo XML esta em rascunho comercial')
        self.assertContains(response, 'Corrigir produto')
        self.assertContains(response, reverse('produtos:produto-update', args=[produto.pk]))

    def test_prontidao_bloqueia_promocao_com_margem_negativa_no_contrato_pdv(self):
        categoria = self.criar_categoria('Polpas comerciais')
        produto = self.criar_produto('Produto promo negativa')
        produto.categoria = categoria
        produto.codigo_barras = '7891000000999'
        produto.preco_custo = Decimal('10.00')
        produto.preco_custo_medio = Decimal('10.00')
        produto.preco_venda = Decimal('12.00')
        produto.preco_promocional = Decimal('8.00')
        produto.rascunho_comercial = False
        produto.calcular_margem()
        produto.save()
        ProdutoFilial.objects.filter(produto=produto, filial=self.filial).update(
            preco_promocional=Decimal('8.00'),
            preco_promocional_ativo=True,
        )

        contrato = contrato_pdv_produto(produto, filial=self.filial)

        self.assertFalse(contrato['pode_vender'])
        self.assertFalse(contrato['pode_promocionar_sem_alerta'])
        self.assertIn('promocao_margem_negativa', [p['codigo'] for p in contrato['pendencias']])

    def test_estoque_exibe_extrato_com_status_comercial(self):
        produto = self.criar_produto('Produto estoque pendente')
        produto.rascunho_comercial = True
        produto.preco_venda = Decimal('0.00')
        produto.preco_custo = Decimal('0.00')
        produto.save(update_fields=['rascunho_comercial', 'preco_venda', 'preco_custo', 'updated_at'])
        Estoque.objects.create(
            produto=produto,
            filial=self.filial,
            quantidade_atual=Decimal('2'),
            quantidade_disponivel=Decimal('2'),
            custo_medio=Decimal('0'),
        )

        request_get = self.request('get', reverse('estoque:estoque-list'))
        response = EstoqueListView.as_view()(request_get)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Extrato')
        self.assertContains(response, 'Extrato (Ficha Kardex)')
        self.assertContains(response, 'Movimentacoes do produto')
        self.assertContains(response, 'data-kardex-more-url')
        self.assertContains(response, reverse('estoque:estoque-kardex-produto', args=[produto.pk]))

        response = EstoqueKardexProdutoView.as_view()(
            self.request('get', reverse('estoque:estoque-kardex-produto', args=[produto.pk])),
            pk=produto.pk,
        )
        payload = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['prontidao']['label'], 'Pendente custo')
        self.assertTrue(any('Sem custo valido' in pendencia for pendencia in payload['prontidao']['pendencias']))
        self.assertTrue(any('rascunho comercial' in pendencia for pendencia in payload['prontidao']['pendencias']))

    def test_entrada_efetivada_bloqueia_edicoes_operacionais(self):
        fornecedor = self.criar_fornecedor(documento='77888999000103')
        produto = self.criar_produto('Produto bloqueio pos efetivacao')
        outro_produto = self.criar_produto('Produto tentativa troca')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-BLOQUEIO-POS',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        item = CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('1'),
            valor_unitario=Decimal('10'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
        )
        CompraService.efetivar_entrada(entrada, self.usuario)

        acoes_bloqueadas = [
            (
                EntradaNFVincularItemView.as_view(),
                reverse('compras:entrada-vincular-item', args=[entrada.pk, item.pk]),
                {'produto': str(outro_produto.pk), 'fator_conversao': '1', 'unidade_estoque': 'UN'},
                {'pk': entrada.pk, 'item_id': item.pk},
            ),
            (
                EntradaNFCriarProdutoItemView.as_view(),
                reverse('compras:entrada-criar-produto-item', args=[entrada.pk, item.pk]),
                {},
                {'pk': entrada.pk, 'item_id': item.pk},
            ),
            (
                EntradaNFReprocessarVinculosView.as_view(),
                reverse('compras:entrada-reprocessar-vinculos', args=[entrada.pk]),
                {},
                {'pk': entrada.pk},
            ),
            (
                EntradaNFCustosView.as_view(),
                reverse('compras:entrada-custos', args=[entrada.pk]),
                {'custo_financeiro': '5.00'},
                {'pk': entrada.pk},
            ),
            (
                EntradaNFDiferencasView.as_view(),
                reverse('compras:entrada-diferencas', args=[entrada.pk]),
                {
                    'item_id': str(item.pk),
                    'quantidade_recebida': '0',
                    'numero_lote': 'ALTERADO',
                    'justificativa_diferenca': 'tentativa indevida',
                },
                {'pk': entrada.pk},
            ),
            (
                AdicionarItemEntradaView.as_view(),
                reverse('compras:entrada-add-item', args=[entrada.pk]),
                {},
                {'pk': entrada.pk},
            ),
        ]

        for view, path, data, kwargs in acoes_bloqueadas:
            request = self.request('post', path, data)
            response = view(request, **kwargs)
            self.assertEqual(response.status_code, 302)

        item.refresh_from_db()
        entrada.refresh_from_db()
        self.assertEqual(entrada.status, EntradaNF.Status.EFETIVADA)
        self.assertEqual(item.produto, produto)
        self.assertEqual(item.quantidade_recebida, Decimal('1.000'))
        self.assertEqual(item.numero_lote, '')
        self.assertEqual(item.custo_unitario_total, Decimal('10.0000'))

    def test_finalizacao_oculta_acao_sem_permissao_de_aprovar_compras(self):
        perfil_operador = PerfilAcesso.objects.create(
            empresa=self.empresa,
            nome='Operador sem aprovacao',
            is_admin=False,
        )
        usuario_operador = Usuario.objects.create_user(
            email='operador-sem-aprovar@inoovated.com',
            nome='Operador Entrada',
            password='teste1234',
            empresa=self.empresa,
            filial=self.filial,
            perfil=perfil_operador,
        )
        Permissao.objects.create(
            perfil=perfil_operador,
            modulo='compras',
            pode_ver=True,
            pode_editar=True,
            pode_aprovar=False,
        )
        fornecedor = self.criar_fornecedor()
        produto = self.criar_produto('Produto finalizacao sem aprovacao')
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=fornecedor,
            numero_nf='NF-SEM-APROVAR',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
        )
        CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('1'),
            valor_unitario=Decimal('10'),
            unidade_xml='UN',
            fator_conversao=Decimal('1'),
        )
        entrada.status = EntradaNF.Status.CONFERIDA
        entrada.save(update_fields=['status', 'updated_at'])

        path = reverse('compras:entrada-finalizacao', args=[entrada.pk])
        request = self.request('get', path)
        request.user = usuario_operador
        response = EntradaNFFinalizacaoView.as_view()(request, pk=entrada.pk)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse('compras:entrada-efetivar', args=[entrada.pk]))
        self.assertContains(response, 'Compras: Aprovar')

    def test_fornecedor_pendente_cria_fornecedor_pelo_xml_e_atualiza_equivalencias(self):
        produto = self.criar_produto()
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=get_fornecedor_padrao(self.filial),
            numero_nf='NF-FORNPEND',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
            origem_entrada=EntradaNF.OrigemEntrada.XML,
            fornecedor_pendente=True,
            dados_emitente_xml={
                'documento': '11222333000144',
                'razao_social': 'Fornecedor XML LTDA',
                'nome_fantasia': 'Fornecedor XML',
                'ie': '200000000',
                'endereco': 'Rua Teste 10',
                'municipio': 'Natal',
                'uf': 'RN',
                'cep': '59000000',
                'telefone': '84999990000',
            },
        )
        equivalencia = ProdutoFornecedorEquivalencia.objects.create(
            fornecedor=None,
            fornecedor_cnpj_xml='11222333000144',
            fornecedor_razao_social_xml='Fornecedor XML LTDA',
            produto=produto,
            codigo_fornecedor='FORN-001',
            ean_utilizado='7891000000001',
            unidade_compra='CX',
            unidade_estoque='UN',
            fator_conversao=Decimal('12'),
            ultimo_custo=Decimal('2.5000'),
            data_ultima_compra=entrada.data_emissao_nf,
        )

        path = reverse('compras:entrada-fornecedor-pendente', args=[entrada.pk])
        request = self.request('post', path, {'acao': 'criar_xml'})
        response = EntradaNFFornecedorPendenteView.as_view()(request, pk=entrada.pk)

        entrada.refresh_from_db()
        equivalencia.refresh_from_db()
        fornecedor = Fornecedor.objects.get(cpf_cnpj='11222333000144')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(entrada.fornecedor_pendente)
        self.assertEqual(entrada.fornecedor, fornecedor)
        self.assertEqual(fornecedor.razao_social, 'Fornecedor XML LTDA')
        self.assertTrue(FornecedorFilial.objects.filter(fornecedor=fornecedor, filial=self.filial).exists())
        self.assertEqual(equivalencia.fornecedor, fornecedor)
