"""
Cadastro rápido de produto, de dentro da tela de ficha técnica.

"Quero criar fichas técnicas de camisas que vão para produção e não pela
camisas que já estão cadastrada nos produtos" -- a tela de nova ficha
exigia escolher um `ProdutoModa` já existente. Agora a caixa de busca do
produto tem um atalho "+ Cadastrar produto novo" que cria o produto (só
código, nome, malha e grade -- o resto completa depois) sem sair da tela
e sem passar pelo cadastro completo.
"""
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.moda.models import Grade, ProdutoModa, Tecido


class ProdutoRapidoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Rapido LTDA', nome_fantasia='Rapido',
            cnpj='83345678000191', segmento='moda_confeccao',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='83345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='rapido@teste.local', nome='Fulano', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.tecido = Tecido.objects.create(filial=cls.filial, nome='PV')
        cls.grade = Grade.objects.create(filial=cls.filial, nome='Adulto')

    def setUp(self):
        self.client.force_login(self.usuario)


class FichaFormOfereceAtalhoTests(ProdutoRapidoBase):

    def test_tela_de_nova_ficha_oferece_cadastrar_produto_na_hora(self):
        # Com ao menos um produto já cadastrado, a tela mostra a caixa de
        # busca (e não a mensagem de catálogo vazio) — é dentro dela que
        # o atalho "+ Cadastrar produto novo" vive.
        ProdutoModa.objects.create(filial=self.filial, codigo='JA001', nome='Já existente')

        html = self.client.get(reverse('moda:ficha-create')).content.decode()

        self.assertIn('+ Cadastrar produto novo', html)
        self.assertIn('produto-rapido', html)


class ProdutoRapidoJsonViewTests(ProdutoRapidoBase):

    def _post(self, **dados):
        base = {'codigo': 'CAM001', 'nome': 'Camisa de jogo'}
        base.update(dados)
        return self.client.post(reverse('moda:produto-rapido-json'), base)

    def test_cria_produto_so_com_codigo_e_nome(self):
        resp = self._post()

        self.assertEqual(resp.status_code, 200)
        dados = resp.json()
        self.assertTrue(dados['ok'])
        produto = ProdutoModa.objects.get(filial=self.filial, codigo='CAM001')
        self.assertEqual(produto.nome, 'Camisa de jogo')
        self.assertEqual(dados['produto']['valor'], f'moda:{produto.pk}')

    def test_cria_produto_com_tecido_e_grade(self):
        resp = self._post(tecido=self.tecido.pk, grade=self.grade.pk)

        self.assertTrue(resp.json()['ok'])
        produto = ProdutoModa.objects.get(filial=self.filial, codigo='CAM001')
        self.assertEqual(produto.tecido_id, self.tecido.pk)
        self.assertEqual(produto.grade_id, self.grade.pk)

    def test_produto_criado_ja_sai_pronto_para_a_caixa_de_busca_escolher(self):
        resp = self._post(tecido=self.tecido.pk, grade=self.grade.pk)

        produto_json = resp.json()['produto']
        self.assertEqual(produto_json['nome'], 'Camisa de jogo')
        self.assertEqual(produto_json['codigo'], 'CAM001')
        self.assertEqual(produto_json['tecido'], 'PV')
        self.assertEqual(produto_json['grade'], 'Adulto')
        self.assertEqual(produto_json['origem'], 'confeccao')

    def test_codigo_repetido_na_filial_e_rejeitado(self):
        ProdutoModa.objects.create(filial=self.filial, codigo='CAM001', nome='Outra')

        resp = self._post()

        self.assertEqual(resp.status_code, 400)
        dados = resp.json()
        self.assertFalse(dados['ok'])
        self.assertIn('codigo', dados['erros'])

    def test_sem_nome_e_rejeitado(self):
        resp = self._post(nome='')

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['ok'])

    def test_mesmo_codigo_em_filiais_diferentes_nao_colide(self):
        outra_filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Outra', cnpj='83345678000353',
            uf='RN', cidade='Mossoró',
        )
        ProdutoModa.objects.create(filial=outra_filial, codigo='CAM001', nome='De outra filial')

        resp = self._post()

        self.assertTrue(resp.json()['ok'])
