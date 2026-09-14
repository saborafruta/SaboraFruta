from decimal import Decimal

from django.test import TestCase

from apps.core.models import Empresa
from apps.core.services.exceptions import DadosInvalidosError
from apps.produtos.models import UnidadeMedida
from apps.produtos.services.conversao import quantizar, quantizar_para_unidade


class QuantizarTests(TestCase):
    def test_arredonda_para_baixo(self):
        self.assertEqual(quantizar(Decimal('1.2344'), 3), Decimal('1.234'))

    def test_arredonda_para_cima_round_half_up(self):
        # ROUND_HALF_UP: 0.5 sempre sobe, mesmo em digito par (diferente do
        # ROUND_HALF_EVEN, que e o default do Decimal puro).
        self.assertEqual(quantizar(Decimal('1.2345'), 3), Decimal('1.235'))
        self.assertEqual(quantizar(Decimal('2.5'), 0), Decimal('3'))

    def test_zero_casas_decimais_arredonda_para_inteiro(self):
        self.assertEqual(quantizar(Decimal('3.6'), 0), Decimal('4'))
        self.assertEqual(quantizar(Decimal('3.4'), 0), Decimal('3'))

    def test_aceita_string_alem_de_decimal(self):
        self.assertEqual(quantizar('1.2345', 3), Decimal('1.235'))

    def test_casas_decimais_negativo_e_invalido(self):
        with self.assertRaises(DadosInvalidosError):
            quantizar(Decimal('1'), -1)


class QuantizarParaUnidadeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Conversao LTDA',
            cnpj='71345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.un = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade', casas_decimais=0,
        )
        cls.kg = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='KG', descricao='Quilograma', casas_decimais=3,
        )

    def test_respeita_casas_decimais_da_unidade_de_contagem(self):
        self.assertEqual(quantizar_para_unidade(Decimal('2.5'), self.un), Decimal('3'))

    def test_respeita_casas_decimais_da_unidade_de_peso(self):
        self.assertEqual(quantizar_para_unidade(Decimal('1.23456'), self.kg), Decimal('1.235'))
