"""
"+ Nova unidade" no meio de outro formulário (o de produto de estoque da
Moda, por exemplo): criar a unidade sem perder o que já estava
preenchido na tela de origem. `UnidadeInlineCreateView` é a mesma
criação de `UnidadeCreateView`, só que devolve JSON em vez de
redirecionar -- quem chamou insere a opção nova no próprio `<select>`.
"""
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.produtos.models import UnidadeMedida


class UnidadeInlineCreateBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Unidade Inline LTDA', nome_fantasia='Unidade Inline',
            cnpj='93345678000101', regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Empresa Unidade Inline LTDA',
            cnpj='93345678000282', uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='unidade-inline@teste.local', nome='Unidade Inline', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)


class UnidadeInlineCreateViewTests(UnidadeInlineCreateBase):

    def test_cria_e_devolve_id_e_rotulo_em_json(self):
        resposta = self.client.post(
            reverse('produtos:unidade-inline-create'), {'sigla': 'M', 'descricao': 'Metro'},
        )

        self.assertEqual(resposta.status_code, 200)
        dados = resposta.json()
        self.assertTrue(dados['ok'])
        unidade = UnidadeMedida.objects.get(pk=dados['id'])
        self.assertEqual(unidade.sigla, 'M')
        self.assertEqual(unidade.descricao, 'Metro')
        self.assertEqual(unidade.empresa_id, self.empresa.pk)
        self.assertEqual(dados['label'], str(unidade))

    def test_sem_sigla_recusa_com_json_de_erro(self):
        resposta = self.client.post(
            reverse('produtos:unidade-inline-create'), {'sigla': '', 'descricao': 'Metro'},
        )

        self.assertEqual(resposta.status_code, 400)
        dados = resposta.json()
        self.assertFalse(dados['ok'])
        self.assertIn('error', dados)
        self.assertFalse(UnidadeMedida.objects.filter(descricao='Metro').exists())

    def test_nao_deixa_repetir_sigla_da_mesma_empresa(self):
        UnidadeMedida.objects.create(empresa=self.empresa, sigla='M', descricao='Metro')

        resposta = self.client.post(
            reverse('produtos:unidade-inline-create'), {'sigla': 'M', 'descricao': 'Metro de novo'},
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(UnidadeMedida.objects.filter(sigla='M', empresa=self.empresa).count(), 1)
