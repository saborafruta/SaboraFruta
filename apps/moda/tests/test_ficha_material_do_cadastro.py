"""
Atalho "Escolher do cadastro" no formulário de material da ficha técnica.

O QUE ESTE ATALHO CERCA:

  · A LISTA VEM DO CADASTRO DE AVIAMENTOS, escopada pela filial e só os
    ativos -- é o mesmo catálogo que a tela Engenharia › Cadastro de
    Aviamentos já mantém, não uma segunda fonte;

  · SELECIONAR PREENCHE tipo, descrição, código, unidade e o vínculo de
    estoque (campo oculto) -- o mesmo dado que `AviamentoForm` grava,
    só que pronto pro JavaScript ler e preencher os campos do form de
    material;

  · O ATALHO É OPCIONAL: sem aviamento nenhum cadastrado, a tela
    continua igual a antes -- digitar tudo na mão;

  · O CAMPO OCULTO carrega o vínculo de estoque até a POST: é o que faz
    o material da ficha nascer já ligado ao produto de estoque do
    aviamento escolhido, sem precisar editar depois.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.constants.segmentos import MODA_CONFECCAO
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.moda.models import Aviamento, FichaTecnica, MaterialFicha, ProdutoModa
from apps.produtos.models import Produto, UnidadeMedida


class FichaMaterialDoCadastroBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Aviamento Ficha LTDA', nome_fantasia='Ficha',
            cnpj='83345678000191', segmento=MODA_CONFECCAO,
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Aviamento Ficha LTDA',
            cnpj='83345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='ficha-aviamento@moda.local', nome='Ficha', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.produto = ProdutoModa.objects.create(
            filial=cls.filial, codigo='CAM101', nome='Camisa polo',
        )
        cls.ficha = FichaTecnica.objects.create(
            filial=cls.filial, produto=cls.produto,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse('moda:ficha-detail', args=[self.ficha.pk])

    def _produto_estoque(self, codigo='ZIP001'):
        unidade = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        return Produto.objects.create(
            filial=self.filial, codigo=codigo, descricao='Zíper nylon nº 5 preto',
            unidade_medida=unidade,
        )

    def _aviamento(self, nome='Zíper nylon nº 5 preto', ativo=True, produto_estoque=None):
        return Aviamento.objects.create(
            filial=self.filial, nome=nome, tipo=Aviamento.Tipo.ZIPER,
            codigo='ZIP-5-PT', unidade=Aviamento.Unidade.PECA,
            produto_estoque=produto_estoque, ativo=ativo,
        )


class ListaDeAviamentosNaFichaTests(FichaMaterialDoCadastroBase):

    def test_a_tela_mostra_o_atalho_quando_ha_aviamento_cadastrado(self):
        self._aviamento()

        html = self.client.get(self.url).content.decode()

        self.assertIn('Escolher do cadastro', html)
        self.assertIn('Zíper nylon nº 5 preto', html)

    def test_a_tela_nao_mostra_o_atalho_sem_aviamento_nenhum(self):
        html = self.client.get(self.url).content.decode()

        self.assertNotIn('Escolher do cadastro', html)

    def test_aviamento_inativo_nao_aparece_no_atalho(self):
        self._aviamento(nome='Zíper Inativo', ativo=False)

        html = self.client.get(self.url).content.decode()

        self.assertNotIn('Zíper Inativo', html)

    def test_aviamento_de_outra_filial_nao_aparece(self):
        outra_filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Outra', cnpj='83345678000273',
            uf='RN', cidade='Mossoró',
        )
        Aviamento.objects.create(
            filial=outra_filial, nome='Zíper de Mossoró', tipo=Aviamento.Tipo.ZIPER,
        )

        html = self.client.get(self.url).content.decode()

        self.assertNotIn('Zíper de Mossoró', html)

    def test_o_json_leva_tipo_codigo_unidade_e_produto_de_estoque(self):
        produto = self._produto_estoque()
        aviamento = self._aviamento(produto_estoque=produto)

        resposta = self.client.get(self.url)

        dados = resposta.context['aviamentos_json']
        self.assertEqual(len(dados), 1)
        self.assertEqual(dados[0]['id'], aviamento.pk)
        self.assertEqual(dados[0]['tipo'], Aviamento.Tipo.ZIPER)
        self.assertEqual(dados[0]['codigo'], 'ZIP-5-PT')
        self.assertEqual(dados[0]['unidade'], Aviamento.Unidade.PECA)
        self.assertEqual(dados[0]['produto_estoque_id'], produto.pk)


class PostComProdutoDeEstoqueTests(FichaMaterialDoCadastroBase):
    """
    O campo oculto que o atalho preenche é só mais um campo do form comum
    -- POST direto nele (como o JS faz) grava o vínculo de estoque igual
    a se tivesse vindo do `AviamentoForm`.
    """

    def test_material_nasce_com_produto_de_estoque_quando_o_post_traz(self):
        produto = self._produto_estoque()

        self.client.post(
            reverse('moda:ficha-material-add', args=[self.ficha.pk]),
            {
                'tipo': MaterialFicha.Tipo.ZIPER,
                'descricao': 'Zíper nylon nº 5 preto',
                'codigo': 'ZIP-5-PT',
                'unidade': MaterialFicha.Unidade.PECA,
                'produto_estoque': produto.pk,
                'consumo': '1',
                'perda': '0',
                'custo_unitario': '2.50',
            },
        )

        material = MaterialFicha.objects.get(ficha=self.ficha)
        self.assertEqual(material.produto_estoque_id, produto.pk)

    def test_sem_produto_de_estoque_continua_gravando_sem_vinculo(self):
        """A digitação manual (atalho não usado) continua funcionando igual."""
        self.client.post(
            reverse('moda:ficha-material-add', args=[self.ficha.pk]),
            {
                'tipo': MaterialFicha.Tipo.LINHA,
                'descricao': 'Linha 402 branca',
                'codigo': '',
                'unidade': MaterialFicha.Unidade.CONE,
                'produto_estoque': '',
                'consumo': '1',
                'perda': '0',
                'custo_unitario': '5.00',
            },
        )

        material = MaterialFicha.objects.get(ficha=self.ficha)
        self.assertIsNone(material.produto_estoque_id)
