from datetime import date
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.core.services.calendario import adicionar_dias_uteis_bancarios, proximo_dia_util


class CalendarioFinanceiroTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        empresa = SimpleNamespace(uf='RN', cidade='Natal', codigo_municipio_ibge='2408102')
        cls.filial = SimpleNamespace(
            uf='RN', cidade='Natal', codigo_municipio_ibge='2408102', empresa=empresa,
        )

    def test_sabado_e_permitido(self):
        self.assertEqual(proximo_dia_util(date(2026, 8, 29), self.filial), date(2026, 8, 29))

    def test_domingo_avanca_para_segunda(self):
        self.assertEqual(proximo_dia_util(date(2026, 8, 30), self.filial), date(2026, 8, 31))

    def test_feriado_nacional_avanca(self):
        self.assertEqual(proximo_dia_util(date(2026, 9, 7), self.filial), date(2026, 9, 8))

    def test_feriado_estadual_no_sabado_avanca_ate_segunda(self):
        self.assertEqual(proximo_dia_util(date(2026, 10, 3), self.filial), date(2026, 10, 5))

    def test_feriado_municipal_de_natal_avanca(self):
        self.assertEqual(proximo_dia_util(date(2026, 1, 6), self.filial), date(2026, 1, 7))

    def test_feriados_municipais_moveis_de_natal_avancam(self):
        self.assertEqual(proximo_dia_util(date(2026, 4, 3), self.filial), date(2026, 4, 4))
        self.assertEqual(proximo_dia_util(date(2026, 6, 4), self.filial), date(2026, 6, 5))

    def test_compensacao_bancaria_nao_conta_sabado_e_domingo(self):
        self.assertEqual(
            adicionar_dias_uteis_bancarios(date(2026, 8, 21), 1, self.filial),
            date(2026, 8, 24),
        )

    def test_compensacao_bancaria_pula_feriado(self):
        self.assertEqual(
            adicionar_dias_uteis_bancarios(date(2026, 9, 4), 1, self.filial),
            date(2026, 9, 8),
        )
