"""
Aviamentos — o que precisa estar certo é o AGRUPAMENTO.

Juntar demais esconde um cadastro duplicado; juntar de menos inventa um
aviamento que não existe. Nos dois casos a tela mente sem dar sinal, e por
isso a regra vive fora da view, numa função que dá para testar.
"""
from decimal import Decimal

from django.test import TestCase

from apps.core.models import Empresa, Filial
from apps.moda.models import FichaTecnica, MaterialFicha, ProdutoModa
from apps.moda.views_insumos import TIPOS_AVIAMENTO, agrupar


class AgrupamentoAviamentosTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Aviamento LTDA', nome_fantasia='Aviamento',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Aviamento LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.camisa = cls._ficha('CAM001', 'Camisa de jogo')
        cls.calcao = cls._ficha('CAL001', 'Calcao de jogo')

    @classmethod
    def _ficha(cls, codigo, nome):
        produto = ProdutoModa.objects.create(
            filial=cls.filial, codigo=codigo, nome=nome,
        )
        return FichaTecnica.objects.create(filial=cls.filial, produto=produto)

    @staticmethod
    def _material(ficha, **campos):
        campos.setdefault('tipo', MaterialFicha.Tipo.ZIPER)
        campos.setdefault('descricao', 'Ziper nylon n5')
        campos.setdefault('unidade', MaterialFicha.Unidade.UNIDADE)
        campos.setdefault('consumo', Decimal('1'))
        return MaterialFicha.objects.create(ficha=ficha, **campos)

    def _agrupar(self):
        return agrupar(
            MaterialFicha.objects
            .filter(ficha__filial=self.filial, tipo__in=TIPOS_AVIAMENTO)
            .select_related('ficha', 'ficha__produto', 'produto_estoque')
        )

    # ── Agrupamento ──────────────────────────────────────────────────────

    def test_mesmo_codigo_em_fichas_diferentes_vira_um_grupo(self):
        self._material(self.camisa, codigo='ZIP-5')
        self._material(self.calcao, codigo='ZIP-5')

        grupos = self._agrupar()

        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]['qtd_usos'], 2)

    def test_codigo_manda_mesmo_com_descricao_diferente(self):
        """O estoque e o fornecedor reconhecem o código, não o texto."""
        self._material(self.camisa, codigo='ZIP-5', descricao='Ziper nylon n5')
        self._material(self.calcao, codigo='ZIP-5', descricao='ZIPER NYLON No 5')

        self.assertEqual(len(self._agrupar()), 1)

    def test_sem_codigo_descricao_diferente_fica_separada(self):
        """
        Separar é o comportamento certo: juntar por semelhança esconderia
        justamente o cadastro que precisa ser padronizado.
        """
        self._material(self.camisa, descricao='Ziper nylon n5')
        self._material(self.calcao, descricao='Ziper nylon numero 5')

        self.assertEqual(len(self._agrupar()), 2)

    def test_descricao_ignora_caixa_e_espaco(self):
        self._material(self.camisa, descricao='Ziper nylon n5')
        self._material(self.calcao, descricao='  ZIPER NYLON N5  ')

        self.assertEqual(len(self._agrupar()), 1)

    def test_tipos_diferentes_nao_se_juntam(self):
        self._material(self.camisa, tipo=MaterialFicha.Tipo.ZIPER, descricao='Preto')
        self._material(self.camisa, tipo=MaterialFicha.Tipo.BOTAO, descricao='Preto')

        self.assertEqual(len(self._agrupar()), 2)

    def test_tecido_fica_de_fora(self):
        """Tecido é material, não aviamento — a outra tela cuida dele."""
        self._material(self.camisa, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
                       descricao='Malha Dry Fit')
        self._material(self.camisa, tipo=MaterialFicha.Tipo.LINHA, descricao='Linha 120')

        grupos = self._agrupar()

        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]['descricao'], 'Linha 120')

    # ── Achados ──────────────────────────────────────────────────────────

    def test_preco_divergente_e_apontado_com_os_dois_valores(self):
        self._material(self.camisa, codigo='ZIP-5', custo_unitario=Decimal('1.50'))
        self._material(self.calcao, codigo='ZIP-5', custo_unitario=Decimal('2.10'))

        grupo = self._agrupar()[0]

        self.assertTrue(grupo['preco_divergente'])
        self.assertEqual(grupo['precos'], [Decimal('1.50'), Decimal('2.10')])

    def test_mesmo_preco_nao_e_divergencia(self):
        self._material(self.camisa, codigo='ZIP-5', custo_unitario=Decimal('1.50'))
        self._material(self.calcao, codigo='ZIP-5', custo_unitario=Decimal('1.50'))

        self.assertFalse(self._agrupar()[0]['preco_divergente'])

    def test_sem_vinculo_com_estoque_e_apontado(self):
        self._material(self.camisa, codigo='ZIP-5')
        self.assertTrue(self._agrupar()[0]['sem_estoque'])

    # ── Somas ────────────────────────────────────────────────────────────

    def test_consumo_soma_as_fichas_com_a_perda(self):
        self._material(self.camisa, codigo='ZIP-5',
                       consumo=Decimal('2'), perda=Decimal('10'))
        self._material(self.calcao, codigo='ZIP-5', consumo=Decimal('1'))

        # 2 + 10% = 2,2 ; mais 1 sem perda
        self.assertEqual(self._agrupar()[0]['consumo_total'], Decimal('3.2000'))
