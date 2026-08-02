import json
from decimal import Decimal

from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.produtos.models import (
    CategoriaProduto,
    CategoriaProdutoFilial,
    ClasseFiscal,
    ClasseFiscalAliquota,
    ClasseFiscalFilial,
    Produto,
    ProdutoFilial,
    UnidadeMedida,
    UnidadeMedidaFilial,
)
from apps.produtos.views.produto import ProdutoFiscalListView, ProdutoInlineEditView, _produto_fiscal_pendencias


class ProdutoFiscalListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Fiscal Produto LTDA',
            nome_fantasia='Empresa Fiscal Produto',
            cnpj='91345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Fiscal Produto',
            nome_fantasia='Filial Fiscal',
            cnpj='91345678000192',
            uf='RN',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Administrador',
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='produto-fiscal@inoovated.com',
            nome='Usuario Produto Fiscal',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa,
            sigla='UN',
            descricao='Unidade',
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.categoria = CategoriaProduto.objects.create(
            empresa=cls.empresa,
            filial=cls.filial,
            nome='Polpas',
        )
        CategoriaProdutoFilial.objects.create(categoria=cls.categoria, filial=cls.filial)
        cls.classe_fiscal = ClasseFiscal.objects.create(
            empresa=cls.empresa,
            codigo='POLPA',
            descricao='Polpas tributadas',
            cst_icms_padrao='102',
            cst_pis_padrao='01',
            cst_cofins_padrao='01',
        )
        ClasseFiscalFilial.objects.create(classe_fiscal=cls.classe_fiscal, filial=cls.filial)
        ClasseFiscalAliquota.objects.create(
            classe_fiscal=cls.classe_fiscal,
            uf_destino='RN',
            pis=Decimal('0.65'),
            cofins=Decimal('3.00'),
            vigencia_inicio=timezone.localdate(),
        )

    def setUp(self):
        self.factory = RequestFactory()

    def request(self, params=None):
        request = self.factory.get('/produtos/fiscal/', params or {})
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = self.client.session
        request._messages = FallbackStorage(request)
        return request

    def post_inline(self, produto, field, value):
        request = self.factory.post(
            f'/produtos/{produto.pk}/inline-editar/',
            {'field': field, 'value': value},
        )
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = self.client.session
        request._messages = FallbackStorage(request)
        return ProdutoInlineEditView.as_view()(request, pk=produto.pk)

    def criar_produto(self, **kwargs):
        ativo_filial = kwargs.pop('ativo_filial', True)
        dados = {
            'filial': self.filial,
            'unidade_medida': self.unidade,
            'categoria': self.categoria,
            'classe_fiscal': self.classe_fiscal,
            'descricao': 'Polpa Fiscal Completa',
            'codigo': 'PF001',
            'codigo_barras': '7891000000001',
            'ncm': '20089900',
            'cest': '1700100',
            'cfop_venda_interna': '5102',
            'cfop_venda_interestadual': '6102',
            'cfop_compra': '1102',
            'cfop_devolucao': '5202',
            'cfop_devolucao_compra': '1202',
            'cst_csosn': '102',
            'cst_pis': '01',
            'cst_cofins': '01',
            'cst_ipi': '99',
            'codigo_enquadramento_ipi': '999',
            'aliquota_ipi': Decimal('5.00'),
            'preco_venda': Decimal('10.00'),
            'preco_custo': Decimal('4.00'),
        }
        dados.update(kwargs)
        produto = Produto.objects.create(**dados)
        ProdutoFilial.objects.create(produto=produto, filial=self.filial, ativo=ativo_filial)
        return produto

    def renderizar(self, params=None):
        return ProdutoFiscalListView.as_view()(self.request(params))

    def test_lista_fiscal_renderiza_dados_tributarios_do_produto(self):
        produto = self.criar_produto()

        response = self.renderizar()
        content = response.content.decode('utf-8')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Polpa Fiscal Completa', content)
        self.assertIn('20089900', content)
        self.assertIn('5102', content)
        self.assertIn('6102', content)
        self.assertIn('CFOP interno', content)
        self.assertIn('CFOP externo', content)
        self.assertNotIn('Comp.', content)
        self.assertIn('CST', content)
        self.assertIn('Simples Nacional', content)
        self.assertIn('PIS', content)
        self.assertIn('COFINS', content)
        self.assertIn('Aliq.', content)
        self.assertNotIn('Enq.', content)
        self.assertIn(f'/produtos/{produto.pk}/?step=3', content)
        self.assertNotIn('Cls.', content)
        self.assertNotIn('Info padrão</th>', content)
        self.assertNotIn('Status fiscal', content)

    def test_lista_fiscal_usa_regime_da_filial(self):
        self.criar_produto()
        self.filial.regime_tributario = Empresa.RegimeTributario.LUCRO_PRESUMIDO
        self.filial.codigo_regime_tributario = 3
        self.filial.save(update_fields=['regime_tributario', 'codigo_regime_tributario'])

        response = self.renderizar()
        content = response.content.decode('utf-8')

        self.assertIn('Regime Normal', content)
        self.assertNotIn('Simples Nacional', content)

    def test_filtros_fiscais_consideram_ncm_cfop_ipi_e_pis_cofins(self):
        self.criar_produto(descricao='Polpa Fiscal Encontrada')
        self.criar_produto(
            descricao='Produto Fora do Filtro',
            codigo='PF002',
            codigo_barras='7891000000002',
            ncm='21069090',
            cfop_venda_interna='5405',
            cfop_venda_interestadual='6405',
            cfop_compra='1403',
            cst_pis='04',
            cst_cofins='04',
            aliquota_ipi=Decimal('0.00'),
        )

        response = self.renderizar({
            'ncm': '2008.99.00',
            'cfop': '5102',
            'ipi': '5',
            'pis_cofins': '01',
        })
        content = response.content.decode('utf-8')

        self.assertIn('Polpa Fiscal Encontrada', content)
        self.assertNotIn('Produto Fora do Filtro', content)

    def test_pendencias_fiscais_apontam_campos_criticos(self):
        produto = self.criar_produto(
            descricao='Produto com Pendencias',
            ncm='',
            cfop_venda_interestadual='',
            cst_csosn='',
            cst_pis='',
            cst_cofins='',
            classe_fiscal=None,
        )

        pendencias = _produto_fiscal_pendencias(produto)

        self.assertIn('NCM', pendencias)
        self.assertIn('CFOP venda fora UF', pendencias)
        self.assertNotIn('CFOP compra', pendencias)
        self.assertIn('CST/CSOSN', pendencias)
        self.assertIn('CST PIS', pendencias)
        self.assertIn('CST COFINS', pendencias)
        self.assertNotIn('Classe fiscal', pendencias)

    def test_edita_campos_fiscais_pela_listagem(self):
        produto = self.criar_produto()

        response_ncm = self.post_inline(produto, 'ncm', '2106.90.90')
        response_cest = self.post_inline(produto, 'cest', '1700200')
        response_cfop = self.post_inline(produto, 'cfop_venda_interna', '5405')
        response_pis = self.post_inline(produto, 'cst_pis', '04')
        response_cofins = self.post_inline(produto, 'cst_cofins', '04')
        response_aliquota_pis = self.post_inline(produto, 'aliquota_pis', '1,65')
        response_aliquota_cofins = self.post_inline(produto, 'aliquota_cofins', '7,6')
        response_ipi = self.post_inline(produto, 'aliquota_ipi', '7,5')

        produto.refresh_from_db()

        self.assertEqual(response_ncm.status_code, 200)
        self.assertEqual(json.loads(response_ncm.content.decode('utf-8'))['display'], '21069090')
        self.assertEqual(response_cest.status_code, 200)
        self.assertEqual(response_cfop.status_code, 200)
        self.assertEqual(response_pis.status_code, 200)
        self.assertEqual(response_cofins.status_code, 200)
        self.assertEqual(response_aliquota_pis.status_code, 200)
        self.assertEqual(response_aliquota_cofins.status_code, 200)
        self.assertEqual(response_ipi.status_code, 200)
        self.assertEqual(produto.ncm, '21069090')
        self.assertEqual(produto.cest, '1700200')
        self.assertEqual(produto.cfop_venda_interna, '5405')
        self.assertEqual(produto.cst_pis, '04')
        self.assertEqual(produto.cst_cofins, '04')
        self.assertEqual(produto.aliquota_pis, Decimal('1.65'))
        self.assertEqual(produto.aliquota_cofins, Decimal('7.60'))
        self.assertEqual(produto.aliquota_ipi, Decimal('7.50'))
