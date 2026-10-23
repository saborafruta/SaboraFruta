from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from apps.financeiro.services.fluxo_caixa_service import FluxoCaixaService


class FluxoCaixaComparativoTest(SimpleTestCase):
    def test_agrupa_periodo_por_mes_e_calcula_desvio(self):
        serie = [
            {
                'data': date(2026, 1, 31),
                'entradas_previstas': Decimal('100'),
                'entradas_realizadas': Decimal('80'),
                'saidas_previstas': Decimal('30'),
                'saidas_realizadas': Decimal('20'),
            },
            {
                'data': date(2026, 2, 1),
                'entradas_previstas': Decimal('50'),
                'entradas_realizadas': Decimal('70'),
                'saidas_previstas': Decimal('10'),
                'saidas_realizadas': Decimal('15'),
            },
        ]

        meses = FluxoCaixaService._agrupar_meses(
            serie, date(2026, 1, 31), date(2026, 2, 1),
        )

        self.assertEqual([linha['mes'] for linha in meses], [date(2026, 1, 1), date(2026, 2, 1)])
        self.assertEqual(meses[0]['saldo_previsto'], Decimal('70'))
        self.assertEqual(meses[0]['saldo_realizado'], Decimal('60'))
        self.assertEqual(meses[0]['variacao'], Decimal('-10'))
        self.assertEqual(meses[1]['variacao'], Decimal('15'))
