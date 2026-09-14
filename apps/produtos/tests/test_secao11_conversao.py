"""
Secao 11 do documento de especificacao ("Plano de Testes e Criterios de
Aceite") -- casos 1 a 5 (conversao), com os numeros exatos do enunciado,
para rastreabilidade. A logica em si ja era coberta por
test_conversao.py/test_apresentacao.py; este arquivo garante que os
cenarios literais do documento tem um teste com o nome deles.
"""
from decimal import Decimal

from django.test import TestCase

from apps.core.models import Empresa, Filial
from apps.core.services.exceptions import DadosInvalidosError
from apps.produtos.models import Produto, ProdutoApresentacao, UnidadeMedida
from apps.produtos.services.apresentacao_service import ApresentacaoService


class Secao11ConversaoTests(TestCase):
    """Caso 1001 do documento: Produto EMB500 (id 1001), unidade base UN,
    com as apresentacoes UN, PCT100, PCT500 e CX1000."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Secao 11 LTDA', cnpj='71345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL, codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Secao 11', cnpj='71345678000192', uf='RN',
        )
        cls.un = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='UN', descricao='Unidade')
        cls.pct = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='PCT', descricao='Pacote')
        cls.cx = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='CX', descricao='Caixa')
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.un, descricao='Embalagem Pote 500ml',
            codigo='1001', ncm='39235000',
        )
        cls.un_apresentacao = ProdutoApresentacao.objects.create(
            produto=cls.produto, unidade=cls.un, descricao='Unidade', fator_conversao=1,
        )
        cls.pct100 = ProdutoApresentacao.objects.create(
            produto=cls.produto, unidade=cls.pct, descricao='Pacote 100', fator_conversao=100,
        )
        cls.pct500 = ProdutoApresentacao.objects.create(
            produto=cls.produto, unidade=cls.pct, descricao='Pacote 500', fator_conversao=500,
        )
        cls.cx1000 = ProdutoApresentacao.objects.create(
            produto=cls.produto, unidade=cls.cx, descricao='Caixa 1.000', fator_conversao=1000,
        )

    def test_caso_1_1_pct100_para_100_un(self):
        resultado = ApresentacaoService.converter(self.pct100, 1, para='base')
        self.assertEqual(resultado, Decimal('100'))

    def test_caso_2_5_pct100_para_500_un(self):
        resultado = ApresentacaoService.converter(self.pct100, 5, para='base')
        self.assertEqual(resultado, Decimal('500'))

    def test_caso_3_2_pct500_para_1000_un(self):
        resultado = ApresentacaoService.converter(self.pct500, 2, para='base')
        self.assertEqual(resultado, Decimal('1000'))

    def test_caso_4_3_cx1000_para_3000_un(self):
        resultado = ApresentacaoService.converter(self.cx1000, 3, para='base')
        self.assertEqual(resultado, Decimal('3000'))

    def test_caso_5_fator_zero_levanta_erro_de_validacao(self):
        apresentacao_invalida = ProdutoApresentacao(
            produto=self.produto, unidade=self.pct, descricao='Pacote invalido', fator_conversao=0,
        )
        with self.assertRaises(Exception):
            apresentacao_invalida.full_clean()

    def test_caso_5_fator_negativo_levanta_erro_de_validacao(self):
        apresentacao_invalida = ProdutoApresentacao(
            produto=self.produto, unidade=self.pct, descricao='Pacote invalido', fator_conversao=-100,
        )
        with self.assertRaises(Exception):
            apresentacao_invalida.full_clean()

    def test_caso_5_fator_zero_no_service_de_calculo_encadeado(self):
        # calcular_fator_encadeado tambem e' uma via de entrada de fator --
        # zero em qualquer elo da cadeia deve ser rejeitado.
        with self.assertRaises(DadosInvalidosError):
            ApresentacaoService.calcular_fator_encadeado(0, 10)
