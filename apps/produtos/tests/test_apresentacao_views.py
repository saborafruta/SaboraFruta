"""Views de CRUD de ProdutoApresentacao na propria tela do produto (aba
"Apresentacoes" do wizard) -- antes disso a unica forma de cadastrar
apresentacoes era o Django admin."""
from decimal import Decimal

from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.produtos.models import Produto, ProdutoApresentacao, ProdutoFilial, UnidadeMedida
from apps.produtos.views.produto import (
    ProdutoApresentacaoCreateView, ProdutoApresentacaoDeleteView, ProdutoApresentacaoUpdateView,
)


class ProdutoApresentacaoViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Apresentacao View LTDA', cnpj='82345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL, codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Apresentacao View', cnpj='82345678000192', uf='RN',
        )
        perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='apresentacao-view@inoovated.com', nome='Operador', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=perfil,
        )
        cls.un = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='UN', descricao='Unidade')
        cls.cx = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='CX', descricao='Caixa')
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.un, descricao='Produto com Apresentacoes',
            ncm='39235000', codigo='2001',
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)

    def setUp(self):
        self.factory = RequestFactory()

    def request(self, data=None):
        request = self.factory.post('/produtos/apresentacoes/', data or {})
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = self.client.session
        request._messages = FallbackStorage(request)
        return request

    def test_criar_apresentacao(self):
        response = ProdutoApresentacaoCreateView.as_view()(
            self.request({
                'descricao': 'Caixa 1.000', 'unidade': self.cx.pk, 'fator_conversao': '1000',
                'preco_venda': '9000,00', 'preco_minimo': '0,00',
                'permite_venda': 'on', 'permite_compra': 'on', 'permite_estoque': 'on',
                'ativo': 'on',
            }),
            pk=self.produto.pk,
        )
        self.assertEqual(response.status_code, 302)
        apresentacao = ProdutoApresentacao.objects.get(produto=self.produto, descricao='Caixa 1.000')
        self.assertEqual(apresentacao.fator_conversao, Decimal('1000.000000'))
        self.assertEqual(apresentacao.unidade, self.cx)

    def test_criar_apresentacao_com_fator_invalido_nao_cria_nada(self):
        response = ProdutoApresentacaoCreateView.as_view()(
            self.request({'descricao': 'Caixa invalida', 'unidade': self.cx.pk, 'fator_conversao': '0'}),
            pk=self.produto.pk,
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProdutoApresentacao.objects.filter(produto=self.produto).exists())

    def test_editar_apresentacao(self):
        apresentacao = ProdutoApresentacao.objects.create(
            produto=self.produto, unidade=self.cx, descricao='Caixa 500', fator_conversao=500,
        )
        response = ProdutoApresentacaoUpdateView.as_view()(
            self.request({
                'descricao': 'Caixa 600', 'unidade': self.cx.pk, 'fator_conversao': '600',
                'preco_venda': '0,00', 'preco_minimo': '0,00',
                'permite_venda': 'on', 'permite_compra': 'on', 'permite_estoque': 'on',
                'ativo': 'on',
            }),
            pk=self.produto.pk, apresentacao_pk=apresentacao.pk,
        )
        self.assertEqual(response.status_code, 302)
        apresentacao.refresh_from_db()
        self.assertEqual(apresentacao.descricao, 'Caixa 600')
        self.assertEqual(apresentacao.fator_conversao, Decimal('600.000000'))

    def test_excluir_apresentacao(self):
        apresentacao = ProdutoApresentacao.objects.create(
            produto=self.produto, unidade=self.cx, descricao='Caixa 100', fator_conversao=100,
        )
        response = ProdutoApresentacaoDeleteView.as_view()(
            self.request(), pk=self.produto.pk, apresentacao_pk=apresentacao.pk,
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProdutoApresentacao.objects.filter(pk=apresentacao.pk).exists())

    def test_nao_permite_criar_apresentacao_em_produto_de_outra_empresa(self):
        outra_empresa = Empresa.objects.create(
            razao_social='Outra Empresa LTDA', cnpj='82345678000200',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL, codigo_regime_tributario=1,
        )
        outra_filial = Filial.objects.create(
            empresa=outra_empresa, razao_social='Outra Filial', cnpj='82345678000210', uf='RN',
        )
        outro_produto = Produto.objects.create(
            filial=outra_filial, unidade_medida=self.un, descricao='Produto de outra empresa',
            ncm='39235000', codigo='9001',
        )
        with self.assertRaises(Exception):
            ProdutoApresentacaoCreateView.as_view()(
                self.request({'descricao': 'Caixa', 'unidade': self.cx.pk, 'fator_conversao': '10'}),
                pk=outro_produto.pk,
            )
