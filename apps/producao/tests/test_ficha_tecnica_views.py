from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, Permissao, PerfilAcesso, Usuario
from apps.produtos.models import Produto, UnidadeMedida


class FichaTecnicaFormViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Producao Teste LTDA', nome_fantasia='Producao Teste',
            cnpj='56345678000191', regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Producao', nome_fantasia='Filial Producao',
            cnpj='56345678000192', uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='producao@inoovated.com', nome='Usuario Producao', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade', tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade, descricao='Prato Teste',
            ncm='20089900', preco_custo=Decimal('1.00'),
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

    def test_nova_ficha_tecnica_mostra_atalho_para_novo_produto(self):
        response = self.client.get(reverse('producao:ficha-create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Matérias-primas (BOM)')
        self.assertContains(response, reverse('produtos:produto-create'))
        self.assertContains(response, '+ Novo produto')

    def test_editar_ficha_tecnica_tambem_mostra_atalho(self):
        from apps.producao.models import FichaTecnica

        ficha = FichaTecnica.objects.create(
            filial=self.filial, produto_acabado=self.produto, descricao='Ficha Teste',
        )

        response = self.client.get(reverse('producao:ficha-update', args=[ficha.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('produtos:produto-create'))

    def test_usuario_sem_permissao_de_produtos_nao_ve_atalho(self):
        perfil_sem_permissao = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Producao Operador', is_admin=False,
        )
        Permissao.objects.create(
            perfil=perfil_sem_permissao, modulo=Permissao.Modulo.PRODUCAO,
            pode_ver=True, pode_criar=True,
        )
        operador = Usuario.objects.create_user(
            email='operador.producao@inoovated.com', nome='Operador Producao', password='teste1234',
            empresa=self.empresa, filial=self.filial, perfil=perfil_sem_permissao,
        )
        self.client.force_login(operador)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

        response = self.client.get(reverse('producao:ficha-create'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '+ Novo produto')
