"""
Custos — o que precisa estar certo é a SOMA e o achado.

Custo somado errado não trava nada: sai um número plausível e alguém fecha
preço em cima dele. Os dois riscos concretos são contar o mesmo material
duas vezes (materiais da peça entrando de novo pela estrutura) e deixar
passar em silêncio o material sem preço, que entra como zero.
"""
from decimal import Decimal

from django.test import RequestFactory, TestCase

from apps.core.models import Empresa, Filial
from apps.moda.models import (
    EstruturaProduto, FichaTecnica, MaterialFicha, ProdutoModa,
)
from apps.moda.views_custos import CustoListView, _custo_materiais


class CustosTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Custo LTDA', nome_fantasia='Custo',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Custo LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )

    def _produto(self, codigo, nome):
        return ProdutoModa.objects.create(filial=self.filial, codigo=codigo, nome=nome)

    def _ficha(self, produto):
        return FichaTecnica.objects.create(filial=self.filial, produto=produto)

    @staticmethod
    def _material(ficha, custo, consumo='1', perda=None):
        return MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha Dry', unidade=MaterialFicha.Unidade.METRO,
            consumo=Decimal(consumo), perda=Decimal(perda) if perda else Decimal('0'),
            custo_unitario=Decimal(custo),
        )

    def _linhas(self, **params):
        """Roda a view e devolve as linhas prontas, sem passar pelo template."""
        pedido = RequestFactory().get('/moda/engenharia/custos/', params)
        pedido.filial_ativa = self.filial
        contexto = {}

        # O que se testa aqui e' a CONTA, nao o HTML. O espiao guarda o
        # contexto e NAO renderiza: renderizar exigiria `request.user` e os
        # context processors, que nada tem a ver com o custo.
        from django.http import HttpResponse
        import apps.moda.views_custos as modulo

        render_original = modulo.render

        def espiao(request, template, ctx):
            contexto.update(ctx)
            return HttpResponse('')

        modulo.render = espiao
        try:
            CustoListView().get(pedido)
        finally:
            modulo.render = render_original
        return list(contexto['page_obj'].object_list), contexto['resumo']

    # ── Soma ─────────────────────────────────────────────────────────────

    def test_custo_de_materiais_soma_com_a_perda(self):
        produto = self._produto('CAM001', 'Camisa')
        ficha = self._ficha(produto)
        self._material(ficha, custo='10.00', consumo='2', perda='10')

        # 2m + 10% = 2,2m x R$10 = R$22
        self.assertEqual(_custo_materiais(produto), Decimal('22.00'))

    def test_produto_sem_ficha_custa_zero_e_e_apontado(self):
        self._produto('CAL001', 'Calcao sem ficha')

        linhas, resumo = self._linhas()

        self.assertEqual(linhas[0]['total'], Decimal('0.00'))
        self.assertTrue(linhas[0]['sem_ficha'])
        self.assertEqual(resumo['sem_ficha'], 1)

    def test_estrutura_nao_conta_o_material_da_peca_duas_vezes(self):
        """
        O risco concreto da soma: o custo das PARTES é a estrutura inteira
        menos o que já é da própria peça. Sem esse desconto, os materiais do
        pai entrariam de novo e o total sairia inflado.
        """
        conjunto = self._produto('CONJ001', 'Conjunto')
        camisa = self._produto('CAM001', 'Camisa')

        self._material(self._ficha(conjunto), custo='5.00')   # embalagem do conjunto
        self._material(self._ficha(camisa), custo='30.00')    # tecido da camisa
        EstruturaProduto.objects.create(pai=conjunto, componente=camisa)

        linha = {l['produto'].codigo: l for l in self._linhas()[0]}['CONJ001']

        self.assertEqual(linha['materiais'], Decimal('5.00'))
        self.assertEqual(linha['componentes'], Decimal('30.00'))
        self.assertEqual(linha['total'], Decimal('35.00'))

    def test_quantidade_do_componente_multiplica_o_custo(self):
        conjunto = self._produto('CONJ001', 'Conjunto')
        camisa = self._produto('CAM001', 'Camisa')
        self._ficha(conjunto)
        self._material(self._ficha(camisa), custo='30.00')
        EstruturaProduto.objects.create(
            pai=conjunto, componente=camisa, quantidade=Decimal('2'),
        )

        linha = {l['produto'].codigo: l for l in self._linhas()[0]}['CONJ001']

        self.assertEqual(linha['total'], Decimal('60.00'))

    # ── Achado ───────────────────────────────────────────────────────────

    def test_material_sem_preco_e_apontado(self):
        """Entra como zero e o custo sai menor do que é, sem aviso."""
        produto = self._produto('CAM001', 'Camisa')
        ficha = self._ficha(produto)
        self._material(ficha, custo='10.00')
        self._material(ficha, custo='0')

        linhas, resumo = self._linhas()

        self.assertEqual(linhas[0]['sem_preco'], 1)
        self.assertEqual(resumo['sem_preco'], 1)

    def test_custo_unitario_zerado_tambem_conta_como_sem_preco(self):
        produto = self._produto('CAM001', 'Camisa')
        self._material(self._ficha(produto), custo='0')

        self.assertEqual(self._linhas()[0][0]['sem_preco'], 1)

    def test_ficha_completa_nao_e_apontada(self):
        produto = self._produto('CAM001', 'Camisa')
        self._material(self._ficha(produto), custo='10.00')

        linhas, resumo = self._linhas()

        self.assertEqual(linhas[0]['sem_preco'], 0)
        self.assertFalse(linhas[0]['sem_ficha'])
        self.assertEqual(resumo['sem_preco'], 0)

    # ── Ordenação ────────────────────────────────────────────────────────

    def test_ordena_do_maior_custo_para_o_menor(self):
        barato = self._produto('BAR001', 'Barato')
        caro = self._produto('CAR001', 'Caro')
        self._material(self._ficha(barato), custo='5.00')
        self._material(self._ficha(caro), custo='90.00')

        linhas, _ = self._linhas()

        self.assertEqual([l['produto'].codigo for l in linhas], ['CAR001', 'BAR001'])
