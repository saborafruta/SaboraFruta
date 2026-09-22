from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario


class ProdutoCreateViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Produto Novo LTDA', nome_fantasia='Empresa Produto Novo',
            cnpj='92345678000191', regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Produto Novo', nome_fantasia='Filial Produto Novo',
            cnpj='92345678000192', uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='novoproduto@inoovated.com', nome='Usuario Produto', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

    def test_tela_de_novo_produto_carrega_e_campo_de_nome_nao_esta_travado(self):
        response = self.client.get(reverse('produtos:produto-create'))

        self.assertEqual(response.status_code, 200)
        conteudo = response.content.decode()
        self.assertIn('Digite o nome do produto', conteudo)
        self.assertNotIn('disabled', conteudo.split('Digite o nome do produto')[0][-200:])

    def test_tour_de_primeira_visita_pode_ser_fechado_clicando_fora(self):
        response = self.client.get(reverse('produtos:produto-create'))

        conteudo = response.content.decode()
        self.assertIn('@click="stopTour()"', conteudo)
