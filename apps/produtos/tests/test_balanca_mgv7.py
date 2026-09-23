from decimal import Decimal
from types import SimpleNamespace

from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, SimpleTestCase, TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial
from apps.produtos.services.mgv7_export import (
    ErroExportacaoMGV7,
    TAMANHO_LINHA_ITENSMGV_BASICO,
    gerar_itensmgv,
    linha_item_mgv7,
)
from apps.produtos.views.produto import ProdutoBalancaDownloadView, ProdutoBalancaExportView


def produto_fake(**kwargs):
    dados = {
        'codigo_balanca': '42',
        'preco_venda': Decimal('12.34'),
        'descricao': 'Maca Gala Premium',
        'descricao_pdv': 'Maca Gala',
        'vendido_por_peso_granel': True,
        'tipo_produto': 'granel_peso',
    }
    dados.update(kwargs)
    return SimpleNamespace(**dados)


class MGV7ExportServiceTests(SimpleTestCase):
    def test_linha_basica_tem_113_bytes_e_campos_principais(self):
        linha = linha_item_mgv7(produto_fake())

        self.assertEqual(len(linha.encode('ascii')), TAMANHO_LINHA_ITENSMGV_BASICO)
        self.assertTrue(linha.startswith('010000042001234000MACA GALA'))
        self.assertEqual(linha[2], '0')

    def test_produto_unitario_usa_tipo_um_e_arquivo_crlf(self):
        produto = produto_fake(
            codigo_balanca='7',
            vendido_por_peso_granel=False,
            tipo_produto='unitario',
        )
        conteudo = gerar_itensmgv([produto])

        self.assertEqual(conteudo[2:3], b'1')
        self.assertTrue(conteudo.endswith(b'\r\n'))
        self.assertNotIn(b'\n', conteudo[:-2])

    def test_rejeita_plu_duplicado_mesmo_com_zeros_a_esquerda(self):
        with self.assertRaisesRegex(ErroExportacaoMGV7, 'repetido'):
            gerar_itensmgv([
                produto_fake(codigo_balanca='42', descricao='Produto A'),
                produto_fake(codigo_balanca='000042', descricao='Produto B'),
            ])

    def test_rejeita_preco_fora_do_layout(self):
        with self.assertRaisesRegex(ErroExportacaoMGV7, 'excede o limite'):
            linha_item_mgv7(produto_fake(preco_venda=Decimal('10000.00')))


class MGV7ExportViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Balanca LTDA',
            nome_fantasia='Empresa Balanca',
            cnpj='46345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Balanca',
            nome_fantasia='Filial Balanca',
            cnpj='46345678000192',
            uf='RN',
        )
        cls.outra_filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Outra Filial Balanca',
            nome_fantasia='Outra Filial',
            cnpj='46345678000193',
            uf='RN',
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Administrador Balanca',
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='balanca@inoovated.com',
            nome='Usuario Balanca',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa,
            sigla='KG',
            descricao='Quilograma',
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.outra_filial)

    def setUp(self):
        self.factory = RequestFactory()

    def request(self, caminho):
        request = self.factory.get(caminho)
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = self.client.session
        request._messages = FallbackStorage(request)
        return request

    def criar_produto(self, descricao, plu, filial_vinculo):
        produto = Produto.objects.create(
            filial=filial_vinculo,
            unidade_medida=self.unidade,
            descricao=descricao,
            ncm='20089900',
            preco_venda=Decimal('10.50'),
            codigo_balanca=plu,
            vendido_por_peso_granel=True,
            gera_etiqueta_balanca=True,
        )
        ProdutoFilial.objects.create(produto=produto, filial=filial_vinculo, ativo=True)
        return produto

    def test_tela_e_download_respeitam_filial_ativa(self):
        produto = self.criar_produto('Banana da filial', '12', self.filial)
        self.criar_produto('Produto de outra filial', '13', self.outra_filial)

        tela = ProdutoBalancaExportView.as_view()(self.request('/produtos/balanca/'))
        download = ProdutoBalancaDownloadView.as_view()(
            self.request('/produtos/balanca/baixar/itensmgv/')
        )

        self.assertContains(tela, produto.descricao)
        self.assertNotContains(tela, 'Produto de outra filial')
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download['Content-Disposition'], 'attachment; filename="Itensmgv.txt"')
        self.assertIn(b'BANANA DA FILIAL', download.content)
        self.assertNotIn(b'PRODUTO DE OUTRA FILIAL', download.content)
