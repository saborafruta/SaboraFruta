"""
A tela de nova ficha técnica.

O QUE A LARGURA MUDA AQUI

Num container estreito a ficha vira uma fila de campos altos, e o que o
produto ENTREGA para ela — modelo, coleção, tecido, grade — sai de vista
assim que a pessoa desce para a especificação. É justamente ali, escrevendo
como a peça é feita, que a falta de tecido importa: descobrir isso com a
ficha já na fábrica é refazer a peça.

O QUE ESTES TESTES CERCAM:

  · O PAINEL DO QUE VEM DO CADASTRO existe e fica ao lado, não dentro da
    primeira seção;

  · CAMPO VAZIO É DITO. "não informado" é o que faz alguém voltar ao
    cadastro do produto antes de a ficha descer;

  · AS AÇÕES CONTINUAM ALCANÇÁVEIS — formulário longo com botão perdido lá
    embaixo é formulário que ninguém termina;

  · A TELA VAZIA continua explicando o que fazer quando não há produto.
"""
from django.test import TestCase
from django.urls import reverse

from apps.core.constants.segmentos import MODA_CONFECCAO
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.moda.models import FichaTecnica, ProdutoModa


class FichaFormTelaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Ficha LTDA', nome_fantasia='Ficha',
            cnpj='63345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
            segmento=MODA_CONFECCAO,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Ficha LTDA',
            cnpj='63345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='ficha@moda.local', nome='Ficha', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse('moda:ficha-create')

    def _produto(self, codigo='CAM001', nome='Camisa de jogo'):
        return ProdutoModa.objects.create(
            filial=self.filial, codigo=codigo, nome=nome,
        )

    # ── O layout ─────────────────────────────────────────────────────────

    def test_a_tela_ocupa_a_largura_e_tem_a_coluna_de_apoio(self):
        self._produto()

        html = self.client.get(self.url).content.decode()

        self.assertNotIn('max-w-4xl', html)
        self.assertIn('O que vem do cadastro do produto', html)
        self.assertIn('<aside', html)

    def test_o_painel_diz_o_que_falta_antes_de_escolher_o_produto(self):
        self._produto()

        html = self.client.get(self.url).content.decode()

        self.assertIn('Escolha o produto para ver modelo', html)

    def test_campo_vazio_do_produto_aparece_como_nao_informado(self):
        """
        É isso que faz alguém voltar ao cadastro ANTES de a ficha descer para
        a fábrica sem tecido.
        """
        self._produto()

        html = self.client.get(self.url).content.decode()

        self.assertIn('não informado', html)

    def test_as_acoes_ficam_na_tela(self):
        self._produto()

        html = self.client.get(self.url).content.decode()

        self.assertIn('Salvar e ir para os materiais', html)
        self.assertIn('Cancelar', html)
        self.assertIn('A próxima tela é a dos', html)

    # ── As três seções continuam lá ──────────────────────────────────────

    def test_as_secoes_continuam_na_ordem(self):
        self._produto()

        html = self.client.get(self.url).content.decode()
        posicoes = [
            html.index('1. De qual produto é esta ficha'),
            html.index('2. Versão e situação'),
            html.index('3. Como a peça é feita'),
        ]

        self.assertEqual(posicoes, sorted(posicoes))

    # ── A tela vazia ─────────────────────────────────────────────────────

    def test_sem_produto_a_tela_explica_o_que_fazer(self):
        html = self.client.get(self.url).content.decode()

        self.assertIn('Não há produto disponível para receber ficha', html)
        self.assertIn('Cadastrar produto novo', html)

    def test_com_todos_os_produtos_fichados_a_tela_manda_para_a_lista(self):
        """
        Cada produto tem uma ficha só: a saída é editar a que existe, e não
        criar outra.
        """
        produto = self._produto()
        FichaTecnica.objects.create(filial=self.filial, produto=produto)

        html = self.client.get(self.url).content.decode()

        self.assertIn('já têm ficha', html)
        self.assertIn('VER AS FICHAS EXISTENTES', html)
