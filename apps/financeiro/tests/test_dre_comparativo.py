from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.financeiro.services.dre import CAIXA, DREService


def _bloco(total, grupos=None, sem_categoria=Decimal('0')):
    return {
        'total': Decimal(total), 'grupos': grupos or {},
        'sem_categoria': sem_categoria, 'sem_categoria_qtd': 0, 'quantidade': 0,
    }


class DREComparativoMensalTest(SimpleTestCase):
    def test_monta_colunas_e_grupos_que_nao_existiam_em_todos_os_meses(self):
        grupo = {'id': 7, 'codigo': '1.01', 'descricao': 'Vendas', 'total': Decimal('100')}
        periodos = [
            {'receita': _bloco('0'), 'despesa': _bloco('20')},
            {'receita': _bloco('100', {7: grupo}), 'despesa': _bloco('30')},
        ]

        with patch.object(DREService, '_periodo', side_effect=periodos):
            comparativo = DREService._comparativo_mensal(
                filial=object(), competencia=date(2026, 2, 1), regime=CAIXA,
                arvore={}, quantidade=2,
            )

        self.assertEqual(comparativo['colunas'], [date(2026, 1, 1), date(2026, 2, 1)])
        self.assertEqual(comparativo['blocos'][0]['linhas'][0]['valores'], [Decimal('0'), Decimal('100')])
        self.assertEqual(comparativo['resultados'], [Decimal('-20'), Decimal('70')])
        self.assertEqual(comparativo['margens'], [None, Decimal('70.0')])
