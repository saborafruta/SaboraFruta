"""POST /api/produtos/lookup-codigo-barras/{codigo}/ (Fase 12, ver apps/produtos/api/views.py)."""
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import Empresa, Filial, PerfilAcesso, Permissao, Usuario
from apps.estoque.models import Deposito, Estoque
from apps.produtos.models import (
    Produto, ProdutoApresentacao, ProdutoCodigoBarras, ProdutoFilial, UnidadeMedida,
)


class LookupCodigoBarrasBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Rede Lookup LTDA', nome_fantasia='Rede Lookup',
            cnpj='91145678000191', regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Loja A', nome_fantasia='Loja A',
            cnpj='91145678000192', uf='RN', is_matriz=True,
        )
        cls.perfil_admin = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='lookup@inoovated.com', nome='Usuario Lookup', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil_admin,
        )
        cls.un = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='UN', descricao='Unidade')
        cls.cx = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='CX', descricao='Caixa')
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.un, descricao='Embalagem Pote 500ml',
            codigo='1001', codigo_barras='7890000000037', ncm='39235000', ativo=True,
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)
        cls.apresentacao_un = ProdutoApresentacao.objects.create(
            produto=cls.produto, unidade=cls.un, descricao='Unidade', fator_conversao=1,
            preco_venda=Decimal('5.90'), principal_venda=True,
        )
        cls.apresentacao_cx = ProdutoApresentacao.objects.create(
            produto=cls.produto, unidade=cls.cx, descricao='Caixa 1.000', fator_conversao=1000,
            preco_venda=Decimal('4500.00'),
        )
        cls.ean_caixa = ProdutoCodigoBarras.objects.create(
            produto=cls.produto, apresentacao=cls.apresentacao_cx, ean='7899999999999', ativo=True,
        )
        deposito = Deposito.objects.create(filial=cls.filial, nome='Geral', is_padrao=True)
        Estoque.objects.create(
            produto=cls.produto, filial=cls.filial, deposito=deposito,
            quantidade_atual=Decimal('250'), quantidade_disponivel=Decimal('250'),
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_login(self.usuario)
        cache.clear()

    def lookup(self, codigo):
        return self.client.post(
            reverse('produtos_api:lookup_codigo_barras', args=[codigo]),
        )


class LookupResolucaoTests(LookupCodigoBarrasBase):
    def test_lookup_por_ean_vinculado_a_apresentacao_especifica(self):
        response = self.lookup('7899999999999')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['produto']['id'], self.produto.pk)
        self.assertEqual(response.data['apresentacao']['id'], self.apresentacao_cx.pk)
        self.assertEqual(response.data['unidade']['sigla'], 'CX')
        self.assertEqual(response.data['fator'], '1000.000000')
        self.assertEqual(response.data['preco'], '4500.0000')

    def test_lookup_por_ean_sem_apresentacao_vinculada_usa_principal_venda(self):
        ProdutoCodigoBarras.objects.create(produto=self.produto, ean='7891111111118', ativo=True)
        response = self.lookup('7891111111118')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['apresentacao']['id'], self.apresentacao_un.pk)

    def test_lookup_por_codigo_interno_do_produto(self):
        response = self.lookup('1001')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['produto']['id'], self.produto.pk)
        self.assertEqual(response.data['apresentacao']['id'], self.apresentacao_un.pk)

    def test_lookup_por_codigo_barras_principal_do_produto(self):
        response = self.lookup('7890000000037')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['apresentacao']['id'], self.apresentacao_un.pk)

    def test_lookup_ean_inexistente_retorna_404(self):
        response = self.lookup('0000000000000')
        self.assertEqual(response.status_code, 404)

    def test_lookup_traz_estoque_disponivel_da_filial_ativa(self):
        response = self.lookup('1001')
        self.assertEqual(response.data['estoque_disponivel'], '250.000')

    def test_lookup_ean_inativo_nao_resolve(self):
        self.ean_caixa.ativo = False
        self.ean_caixa.save(update_fields=['ativo'])
        response = self.lookup('7899999999999')
        self.assertEqual(response.status_code, 404)

    def test_lookup_sem_login_retorna_401_ou_403(self):
        self.client.logout()
        response = self.lookup('1001')
        self.assertIn(response.status_code, (401, 403))


class LookupCacheTests(LookupCodigoBarrasBase):
    def test_resolucao_e_cacheada_entre_chamadas(self):
        primeira = self.lookup('7899999999999')
        self.assertEqual(primeira.status_code, 200)

        # Muda o vinculo no banco; se o cache nao estivesse valendo, a
        # segunda chamada resolveria diferente.
        self.ean_caixa.apresentacao = self.apresentacao_un
        self.ean_caixa.save(update_fields=['apresentacao'])

        segunda = self.lookup('7899999999999')
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(segunda.data['apresentacao']['id'], self.apresentacao_cx.pk)  # ainda o valor cacheado

    def test_preco_e_estoque_nunca_sao_cacheados_mesmo_com_resolucao_cacheada(self):
        self.lookup('1001')  # popula o cache da resolucao

        Estoque.objects.filter(produto=self.produto, filial=self.filial).update(
            quantidade_atual=Decimal('999'), quantidade_disponivel=Decimal('999'),
        )
        self.apresentacao_un.preco_venda = Decimal('7.77')
        self.apresentacao_un.save(update_fields=['preco_venda'])

        response = self.lookup('1001')
        self.assertEqual(response.data['estoque_disponivel'], '999.000')
        self.assertEqual(response.data['preco'], '7.7700')

    def test_negativo_tambem_e_cacheado(self):
        primeira = self.lookup('9999999999999')
        self.assertEqual(primeira.status_code, 404)

        ProdutoCodigoBarras.objects.create(produto=self.produto, ean='9999999999999', ativo=True)

        segunda = self.lookup('9999999999999')
        self.assertEqual(segunda.status_code, 404)  # ainda 404: negativo cacheado

    def test_resolucao_nao_vaza_entre_empresas_diferentes(self):
        outra_empresa = Empresa.objects.create(
            razao_social='Outra Empresa LTDA', cnpj='91145678000283',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL, codigo_regime_tributario=1,
        )
        outra_filial = Filial.objects.create(
            empresa=outra_empresa, razao_social='Loja Outra', nome_fantasia='Loja Outra',
            cnpj='91145678000364', uf='RN', is_matriz=True,
        )
        outro_perfil = PerfilAcesso.objects.create(empresa=outra_empresa, nome='Admin', is_admin=True)
        outro_usuario = Usuario.objects.create_user(
            email='outraempresa@inoovated.com', nome='Usuario Outra Empresa', password='teste1234',
            empresa=outra_empresa, filial=outra_filial, perfil=outro_perfil,
        )
        outra_unidade = UnidadeMedida.objects.create(empresa=outra_empresa, sigla='UN', descricao='Unidade')
        outro_produto = Produto.objects.create(
            filial=outra_filial, unidade_medida=outra_unidade, descricao='Produto de outra empresa',
            codigo='9001', ncm='39235000',
        )
        ProdutoFilial.objects.create(produto=outro_produto, filial=outra_filial)
        ProdutoApresentacao.objects.create(
            produto=outro_produto, unidade=outra_unidade, descricao='Unidade', fator_conversao=1,
            preco_venda=Decimal('1'), principal_venda=True,
        )
        # Mesmo EAN cadastrado nas duas empresas.
        ProdutoCodigoBarras.objects.create(produto=outro_produto, ean='7899999999999', ativo=True)

        resposta_empresa_1 = self.lookup('7899999999999')
        self.assertEqual(resposta_empresa_1.data['produto']['id'], self.produto.pk)

        self.client.force_login(outro_usuario)
        resposta_empresa_2 = self.lookup('7899999999999')
        self.assertEqual(resposta_empresa_2.data['produto']['id'], outro_produto.pk)
