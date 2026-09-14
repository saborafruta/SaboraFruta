"""API REST de cadastro de produtos e apresentacoes (sessao + RBAC, ver apps/produtos/api/)."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import Empresa, Filial, PerfilAcesso, Permissao, Usuario
from apps.core.models import RegistroAuditoria
from apps.estoque.models import Deposito, Estoque
from apps.produtos.models import (
    ItemTabelaPreco, Produto, ProdutoApresentacao, ProdutoFilial,
    TabelaPreco, TabelaPrecoFilial, UnidadeMedida,
)


class ApiProdutosBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Rede API Produtos LTDA', nome_fantasia='Rede API Produtos',
            cnpj='91145678000191', regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Loja A', nome_fantasia='Loja A',
            cnpj='91145678000192', uf='RN', is_matriz=True,
        )
        cls.perfil_admin = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='api@inoovated.com', nome='Usuario API', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil_admin,
        )
        cls.perfil_sem_permissao = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Sem Permissao', is_admin=False,
        )
        Permissao.objects.create(perfil=cls.perfil_sem_permissao, modulo='produtos')  # tudo False por padrao
        cls.usuario_sem_permissao = Usuario.objects.create_user(
            email='sempermissao@inoovated.com', nome='Usuario Sem Permissao', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil_sem_permissao,
        )
        cls.un = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade', tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        cls.cx = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='CX', descricao='Caixa', tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.un, descricao='Embalagem Pote 500ml',
            codigo='1001', ncm='39235000', preco_venda=Decimal('5'), ativo=True,
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)
        cls.produto_inativo = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.un, descricao='Produto Descontinuado',
            codigo='1002', ncm='39235000', ativo=False,
        )
        ProdutoFilial.objects.create(produto=cls.produto_inativo, filial=cls.filial)
        cls.apresentacao_un = ProdutoApresentacao.objects.create(
            produto=cls.produto, unidade=cls.un, descricao='Unidade', fator_conversao=1,
            principal_venda=True,
        )
        cls.apresentacao_cx = ProdutoApresentacao.objects.create(
            produto=cls.produto, unidade=cls.cx, descricao='Caixa 1.000', fator_conversao=1000,
        )

    def setUp(self):
        self.client = APIClient()


class ApiAutenticacaoTests(ApiProdutosBase):
    def test_sem_login_retorna_401_ou_403(self):
        response = self.client.get(reverse('produtos_api:produtos'))
        self.assertIn(response.status_code, (401, 403))

    def test_sem_permissao_retorna_403(self):
        self.client.force_login(self.usuario_sem_permissao)
        response = self.client.get(reverse('produtos_api:produtos'))
        self.assertEqual(response.status_code, 403)


class ApiListagemProdutosTests(ApiProdutosBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def test_lista_apenas_produtos_ativos_da_empresa(self):
        response = self.client.get(reverse('produtos_api:produtos'), {'ativo': 'true'})
        self.assertEqual(response.status_code, 200)
        codigos = [item['codigo'] for item in response.data['results']]
        self.assertIn('1001', codigos)
        self.assertNotIn('1002', codigos)

    def test_filtro_por_codigo(self):
        response = self.client.get(reverse('produtos_api:produtos'), {'codigo': '1001'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['codigo'], '1001')

    def test_busca_por_descricao(self):
        response = self.client.get(reverse('produtos_api:produtos'), {'busca': 'Pote'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)

    def test_produto_traz_apresentacao_principal_venda(self):
        response = self.client.get(reverse('produtos_api:produtos'), {'codigo': '1001'})
        principal = response.data['results'][0]['apresentacao_principal_venda']
        self.assertIsNotNone(principal)
        self.assertEqual(principal['descricao'], 'Unidade')


class ApiDetalheProdutoTests(ApiProdutosBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def test_detalhe_traz_apresentacoes(self):
        url = reverse('produtos_api:produto_detalhe', args=[self.produto.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        descricoes = {item['descricao'] for item in response.data['apresentacoes']}
        self.assertEqual(descricoes, {'Unidade', 'Caixa 1.000'})

    def test_produto_de_outra_empresa_retorna_404(self):
        outra_empresa = Empresa.objects.create(
            razao_social='Outra Empresa LTDA', cnpj='91145678000283',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL, codigo_regime_tributario=1,
        )
        outra_filial = Filial.objects.create(
            empresa=outra_empresa, razao_social='Loja Outra', cnpj='91145678000364', uf='RN', is_matriz=True,
        )
        outra_unidade = UnidadeMedida.objects.create(empresa=outra_empresa, sigla='UN', descricao='Unidade')
        produto_outra_empresa = Produto.objects.create(
            filial=outra_filial, unidade_medida=outra_unidade, descricao='Produto de outra empresa',
            ncm='39235000',
        )
        ProdutoFilial.objects.create(produto=produto_outra_empresa, filial=outra_filial)
        url = reverse('produtos_api:produto_detalhe', args=[produto_outra_empresa.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


class ApiCriarProdutoTests(ApiProdutosBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def test_cria_produto_com_filial_ativa(self):
        response = self.client.post(reverse('produtos_api:produtos'), {
            'descricao': 'Produto Novo', 'codigo': '2001', 'ncm': '39235000',
            'unidade_medida': self.un.pk, 'preco_venda': '9.90',
        })
        self.assertEqual(response.status_code, 201, response.data)
        produto = Produto.objects.get(codigo='2001')
        self.assertEqual(produto.filial, self.filial)

    def test_criar_produto_sem_permissao_retorna_403(self):
        self.client.force_login(self.usuario_sem_permissao)
        response = self.client.post(reverse('produtos_api:produtos'), {
            'descricao': 'Produto Novo', 'ncm': '39235000', 'unidade_medida': self.un.pk,
        })
        self.assertEqual(response.status_code, 403)

    def test_criar_produto_sem_ncm_e_invalido(self):
        response = self.client.post(reverse('produtos_api:produtos'), {
            'descricao': 'Produto sem NCM', 'unidade_medida': self.un.pk,
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('ncm', response.data)


class ApiApresentacoesTests(ApiProdutosBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def test_lista_apresentacoes_do_produto(self):
        url = reverse('produtos_api:produto_apresentacoes', args=[self.produto.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

    def test_filtra_apresentacoes_por_permite_venda(self):
        ProdutoApresentacao.objects.create(
            produto=self.produto, unidade=self.cx, descricao='Display so estoque', fator_conversao=6000,
            permite_venda=False,
        )
        url = reverse('produtos_api:produto_apresentacoes', args=[self.produto.pk])
        response = self.client.get(url, {'permite_venda': 'true'})
        self.assertEqual(len(response.data), 2)

    def test_cria_apresentacao_para_o_produto(self):
        url = reverse('produtos_api:produto_apresentacoes', args=[self.produto.pk])
        response = self.client.post(url, {
            'unidade': self.cx.pk, 'descricao': 'Caixa 500', 'fator_conversao': '500',
        })
        self.assertEqual(response.status_code, 201, response.data)
        apresentacao = ProdutoApresentacao.objects.get(descricao='Caixa 500')
        self.assertEqual(apresentacao.produto, self.produto)

    def test_criar_apresentacao_com_fator_invalido_e_rejeitada(self):
        url = reverse('produtos_api:produto_apresentacoes', args=[self.produto.pk])
        response = self.client.post(url, {
            'unidade': self.cx.pk, 'descricao': 'Caixa invalida', 'fator_conversao': '0',
        })
        self.assertEqual(response.status_code, 400)


class ApiPatchApresentacaoTests(ApiProdutosBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def test_patch_campo_simples_nao_gera_auditoria_extra(self):
        total_antes = RegistroAuditoria.objects.count()
        url = reverse('produtos_api:apresentacao_detalhe', args=[self.apresentacao_cx.pk])
        response = self.client.patch(url, {'preco_venda': '4500.00'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.apresentacao_cx.refresh_from_db()
        self.assertEqual(self.apresentacao_cx.preco_venda, Decimal('4500.00'))
        self.assertEqual(RegistroAuditoria.objects.count(), total_antes)

    def test_patch_fator_conversao_gera_auditoria(self):
        url = reverse('produtos_api:apresentacao_detalhe', args=[self.apresentacao_cx.pk])
        response = self.client.patch(url, {'fator_conversao': '1200'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.apresentacao_cx.refresh_from_db()
        self.assertEqual(self.apresentacao_cx.fator_conversao, Decimal('1200'))
        registro = RegistroAuditoria.objects.filter(
            objeto_tipo='produtos.produtoapresentacao', objeto_id=self.apresentacao_cx.pk,
        ).latest('criado_em')
        self.assertIn('fator_conversao', registro.objeto_descricao)
        self.assertEqual(registro.dados_anteriores['fator_conversao'], '1000.000000')
        self.assertEqual(registro.dados_novos['fator_conversao'], '1200.000000')

    def test_patch_editar_sem_permissao_retorna_403(self):
        self.client.force_login(self.usuario_sem_permissao)
        url = reverse('produtos_api:apresentacao_detalhe', args=[self.apresentacao_cx.pk])
        response = self.client.patch(url, {'preco_venda': '1'}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_patch_fator_invalido_e_rejeitado(self):
        url = reverse('produtos_api:apresentacao_detalhe', args=[self.apresentacao_cx.pk])
        response = self.client.patch(url, {'fator_conversao': '-5'}, format='json')
        self.assertEqual(response.status_code, 400)


class ApiPrecosEEstoqueTests(ApiProdutosBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tabela = TabelaPreco.objects.create(
            filial=cls.filial, descricao='Varejo', tipo=TabelaPreco.Tipo.VAREJO,
        )
        TabelaPrecoFilial.objects.create(tabela=cls.tabela, filial=cls.filial)
        cls.item_preco = ItemTabelaPreco.objects.create(
            tabela=cls.tabela, produto=cls.produto, preco_unitario=Decimal('5.90'), quantidade_minima=0,
        )
        cls.item_preco_atacado = ItemTabelaPreco.objects.create(
            tabela=cls.tabela, produto=cls.produto, preco_unitario=Decimal('5.50'), quantidade_minima=10,
            desconto_valor=Decimal('0.10'),
        )
        cls.deposito = Deposito.objects.create(filial=cls.filial, nome='Geral', is_padrao=True)
        cls.estoque = Estoque.objects.create(
            produto=cls.produto, filial=cls.filial, deposito=cls.deposito,
            quantidade_atual=Decimal('100'), quantidade_disponivel=Decimal('100'),
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def test_lista_precos_do_produto(self):
        url = reverse('produtos_api:produto_precos', args=[self.produto.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(response.data[0]['tabela']['descricao'], 'Varejo')

    def test_filtra_precos_por_filial_sem_vinculo_retorna_vazio(self):
        outra_filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Loja B', nome_fantasia='Loja B',
            cnpj='91145678000273', uf='RN',
        )
        url = reverse('produtos_api:produto_precos', args=[self.produto.pk])
        response = self.client.get(url, {'filial': outra_filial.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_preco_traz_valor_final_com_desconto_aplicado(self):
        url = reverse('produtos_api:produto_precos', args=[self.produto.pk])
        response = self.client.get(url)
        item_atacado = next(i for i in response.data if i['quantidade_minima'] == '10.000')
        self.assertEqual(item_atacado['valor_final'], '5.40')

    def test_lista_estoque_do_produto(self):
        url = reverse('produtos_api:produto_estoque', args=[self.produto.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['quantidade_atual'], '100.000')
        self.assertEqual(response.data[0]['filial']['nome'], 'Loja A')
        self.assertEqual(response.data[0]['deposito']['nome'], 'Geral')

    def test_filtra_estoque_por_filial(self):
        outra_filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Loja B', nome_fantasia='Loja B',
            cnpj='91145678000273', uf='RN',
        )
        url = reverse('produtos_api:produto_estoque', args=[self.produto.pk])
        response = self.client.get(url, {'filial': outra_filial.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_precos_e_estoque_sem_permissao_retorna_403(self):
        self.client.force_login(self.usuario_sem_permissao)
        url_precos = reverse('produtos_api:produto_precos', args=[self.produto.pk])
        url_estoque = reverse('produtos_api:produto_estoque', args=[self.produto.pk])
        self.assertEqual(self.client.get(url_precos).status_code, 403)
        self.assertEqual(self.client.get(url_estoque).status_code, 403)
