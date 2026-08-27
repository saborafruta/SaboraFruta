from decimal import Decimal

from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Fornecedor, FornecedorFilial
from apps.compras.models import EntradaNF
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.fiscal.models import ManifestoFiscalDocumento, ManifestoFiscalLog
from apps.fiscal.services.manifesto_service import ManifestoFiscalService
from apps.fiscal.views import (
    ManifestoFiscalAnexarXMLView, ManifestoFiscalImportarEntradaView, ManifestoFiscalListView,
)


class ManifestoEntradaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Manifesto LTDA',
            nome_fantasia='Manifesto',
            cnpj='51234567000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Manifesto',
            nome_fantasia='Manifesto RN',
            cnpj='51234567000192',
            uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Admin Manifesto',
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='manifesto-test@inoovated.com',
            nome='Usuario Manifesto',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )

    def setUp(self):
        self.factory = RequestFactory()

    def request(self, method, path, data=None):
        if method == 'post':
            request = self.factory.post(path, data or {})
        else:
            request = self.factory.get(path, data or {})
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def chave(self, numero='000000123', cnpj='11222333000144'):
        return f'242605{cnpj}55001{numero}1123456789'

    def xml_nfe(self, chave, emit_doc='11222333000144', dest_doc='12345678901'):
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
        <CNPJ>{emit_doc}</CNPJ>
        <xNome>Fornecedor Manifesto LTDA</xNome>
        <enderEmit>
          <xLgr>Rua Teste</xLgr>
          <nro>10</nro>
          <xMun>Natal</xMun>
          <UF>RN</UF>
          <CEP>59000000</CEP>
        </enderEmit>
      </emit>
      <dest>
        <CPF>{dest_doc}</CPF>
        <xNome>Documento Operacional</xNome>
      </dest>
      <det nItem="1">
        <prod>
          <cProd>FORN-001</cProd>
          <cEAN>7891000000001</cEAN>
          <xProd>Produto do manifesto</xProd>
          <NCM>20089900</NCM>
          <uCom>UN</uCom>
          <qCom>2.0000</qCom>
          <vUnCom>30.0000</vUnCom>
          <vProd>60.00</vProd>
        </prod>
      </det>
      <total>
        <ICMSTot>
          <vProd>60.00</vProd>
          <vFrete>0.00</vFrete>
          <vSeg>0.00</vSeg>
          <vDesc>0.00</vDesc>
          <vOutro>0.00</vOutro>
          <vIPI>0.00</vIPI>
          <vICMS>0.00</vICMS>
          <vNF>60.00</vNF>
        </ICMSTot>
      </total>
    </infNFe>
  </NFe>
</nfeProc>'''

    def criar_manifesto(self, chave=None, xml_completo=''):
        chave = chave or self.chave()
        return ManifestoFiscalDocumento.objects.create(
            filial=self.filial,
            chave_acesso=chave,
            nsu='15',
            cnpj_emitente=chave[6:20],
            razao_social_emitente='Fornecedor Manifesto LTDA',
            data_emissao=timezone.localdate(),
            valor_total=Decimal('60.00'),
            status_download_xml=(
                ManifestoFiscalDocumento.StatusDownload.XML_BAIXADO
                if xml_completo
                else ManifestoFiscalDocumento.StatusDownload.RESUMO
            ),
            xml_completo=xml_completo,
        )

    def test_importar_manifesto_com_xml_cria_entrada_e_vincula_documento(self):
        chave = self.chave(numero='000000201')
        documento = self.criar_manifesto(chave=chave, xml_completo=self.xml_nfe(chave))

        path = reverse('fiscal:manifesto-importar-entrada', args=[documento.pk])
        request = self.request('post', path)
        response = ManifestoFiscalImportarEntradaView.as_view()(request, pk=documento.pk)

        entrada = EntradaNF.objects.get(chave_acesso_nf=chave)
        documento.refresh_from_db()
        fornecedor = Fornecedor.objects.get(cpf_cnpj='11222333000144')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('compras:entrada-conferencia', args=[entrada.pk]))
        self.assertEqual(documento.entrada_nf, entrada)
        self.assertEqual(documento.status_download_xml, ManifestoFiscalDocumento.StatusDownload.IMPORTADA)
        self.assertEqual(entrada.origem_entrada, EntradaNF.OrigemEntrada.MANIFESTO)
        self.assertFalse(entrada.fornecedor_pendente)
        self.assertEqual(entrada.fornecedor, fornecedor)
        self.assertTrue(entrada.destinatario_documento_diferente)

    def test_importar_manifesto_sem_xml_nao_cria_entrada(self):
        documento = self.criar_manifesto(chave=self.chave(numero='000000202'))

        path = reverse('fiscal:manifesto-importar-entrada', args=[documento.pk])
        request = self.request('post', path)
        response = ManifestoFiscalImportarEntradaView.as_view()(request, pk=documento.pk)

        documento.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('fiscal:manifesto-list'))
        self.assertIsNone(documento.entrada_nf)
        self.assertFalse(EntradaNF.objects.filter(chave_acesso_nf=documento.chave_acesso).exists())

    def test_manifesto_com_entrada_existente_apenas_vincula(self):
        chave = self.chave(numero='000000203')
        fornecedor = Fornecedor.objects.create(
            filial=self.filial,
            tipo_pessoa='J',
            razao_social='Fornecedor existente',
            cpf_cnpj='11222333000144',
            uf='RN',
        )
        FornecedorFilial.objects.create(fornecedor=fornecedor, filial=self.filial)
        entrada = EntradaNF.objects.create(
            filial=self.filial,
            fornecedor=fornecedor,
            numero_nf='203',
            serie_nf='1',
            chave_acesso_nf=chave,
            origem_entrada=EntradaNF.OrigemEntrada.CHAVE,
            data_emissao_nf=timezone.localdate(),
            data_entrada=timezone.now(),
            status=EntradaNF.Status.AGUARDANDO_CONFERENCIA,
            usuario=self.usuario,
            valor_total=Decimal('60.00'),
        )
        documento = self.criar_manifesto(chave=chave)

        path = reverse('fiscal:manifesto-importar-entrada', args=[documento.pk])
        request = self.request('post', path)
        response = ManifestoFiscalImportarEntradaView.as_view()(request, pk=documento.pk)

        documento.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(documento.entrada_nf, entrada)
        self.assertEqual(documento.status_download_xml, ManifestoFiscalDocumento.StatusDownload.IMPORTADA)
        self.assertEqual(EntradaNF.objects.filter(chave_acesso_nf=chave).count(), 1)

    def test_manifesto_recusa_xml_de_outra_chave(self):
        chave_documento = self.chave(numero='000000204')
        chave_xml = self.chave(numero='000000205')
        documento = self.criar_manifesto(
            chave=chave_documento,
            xml_completo=self.xml_nfe(chave_xml),
        )

        with self.assertRaisesMessage(DadosInvalidosError, 'nao pertence'):
            ManifestoFiscalService.importar_entrada(documento, self.usuario)

        self.assertFalse(EntradaNF.objects.filter(chave_acesso_nf=chave_xml).exists())

    def test_anexar_xml_completo_salva_xml_e_libera_importacao(self):
        chave = self.chave(numero='000000206')
        documento = self.criar_manifesto(chave=chave)

        path = reverse('fiscal:manifesto-anexar-xml', args=[documento.pk])
        request = self.request('post', path, {
            'xml_texto': self.xml_nfe(chave),
            'acao': 'salvar',
        })
        response = ManifestoFiscalAnexarXMLView.as_view()(request, pk=documento.pk)

        documento.refresh_from_db()
        log = ManifestoFiscalLog.objects.get(documento=documento, tipo_evento='xml_anexado_local')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('fiscal:manifesto-list'))
        self.assertIn('<nfeProc', documento.xml_completo)
        self.assertEqual(documento.status_download_xml, ManifestoFiscalDocumento.StatusDownload.XML_BAIXADO)
        self.assertEqual(log.codigo_status, 'ERP-LOCAL')
        self.assertEqual(log.retorno_resumo['chave_xml'], chave)
        self.assertIsNone(documento.entrada_nf)

    def test_anexar_xml_recusa_chave_diferente(self):
        chave_documento = self.chave(numero='000000207')
        chave_xml = self.chave(numero='000000208')
        documento = self.criar_manifesto(chave=chave_documento)

        path = reverse('fiscal:manifesto-anexar-xml', args=[documento.pk])
        request = self.request('post', path, {
            'xml_texto': self.xml_nfe(chave_xml),
            'acao': 'salvar',
        })
        response = ManifestoFiscalAnexarXMLView.as_view()(request, pk=documento.pk)

        documento.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'nao pertence')
        self.assertEqual(documento.xml_completo, '')
        self.assertFalse(ManifestoFiscalLog.objects.filter(tipo_evento='xml_anexado_local').exists())

    def test_anexar_xml_e_importar_cria_entrada(self):
        chave = self.chave(numero='000000209')
        documento = self.criar_manifesto(chave=chave)

        path = reverse('fiscal:manifesto-anexar-xml', args=[documento.pk])
        request = self.request('post', path, {
            'xml_texto': self.xml_nfe(chave),
            'acao': 'salvar_importar',
        })
        response = ManifestoFiscalAnexarXMLView.as_view()(request, pk=documento.pk)

        entrada = EntradaNF.objects.get(chave_acesso_nf=chave)
        documento.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('compras:entrada-conferencia', args=[entrada.pk]))
        self.assertEqual(documento.entrada_nf, entrada)
        self.assertEqual(entrada.origem_entrada, EntradaNF.OrigemEntrada.MANIFESTO)

    def test_tela_anexar_xml_renderiza_formulario(self):
        documento = self.criar_manifesto(chave=self.chave(numero='000000210'))

        request = self.request('get', reverse('fiscal:manifesto-anexar-xml', args=[documento.pk]))
        response = ManifestoFiscalAnexarXMLView.as_view()(request, pk=documento.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Anexar XML')
        self.assertContains(response, 'Salvar e importar entrada')

    def test_lista_manifesto_renderiza_acoes_de_entrada(self):
        chave = self.chave(numero='000000211')
        self.criar_manifesto(chave=chave, xml_completo=self.xml_nfe(chave))

        # A TELA GANHOU ABAS e abre em "saidas". As notas recebidas -- e as
        # acoes delas -- moraram para a aba `recebidos`; pedir a pagina sem aba
        # caia na de saidas, onde nada disso aparece.
        request = self.request('get', reverse('fiscal:manifesto-list'),
                               {'aba': 'recebidos'})
        response = ManifestoFiscalListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Importar entrada')
        self.assertContains(response, 'Dar ciencia')

    def test_lista_manifesto_sem_xml_mostra_anexar_xml(self):
        self.criar_manifesto(chave=self.chave(numero='000000212'))

        request = self.request('get', reverse('fiscal:manifesto-list'),
                               {'aba': 'recebidos'})
        response = ManifestoFiscalListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Anexar XML')
