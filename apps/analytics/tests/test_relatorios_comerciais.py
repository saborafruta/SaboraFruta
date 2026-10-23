from decimal import Decimal

from django.test import SimpleTestCase

from apps.analytics.services.relatorios_comerciais import _percentual, _somar_linhas


class RelatoriosComerciaisTest(SimpleTestCase):
    def test_soma_fontes_e_calcula_percentual(self):
        destino = {}
        _somar_linhas(destino, [{
            'produto_id': 1, 'produto__descricao': 'Produto A',
            'quantidade': Decimal('2'), 'faturamento': Decimal('20'),
            'descontos': Decimal('1'), 'custo': Decimal('12'), 'vendas': 1,
        }], 'produto_id', 'produto__descricao')
        _somar_linhas(destino, [{
            'produto_id': 1, 'produto__descricao': 'Produto A',
            'quantidade': Decimal('1'), 'faturamento': Decimal('10'),
            'descontos': Decimal('0'), 'custo': Decimal('4'), 'vendas': 1,
        }], 'produto_id', 'produto__descricao')

        self.assertEqual(destino[1]['faturamento'], Decimal('30'))
        self.assertEqual(destino[1]['custo'], Decimal('16'))
        self.assertEqual(destino[1]['vendas'], 2)
        self.assertEqual(_percentual(Decimal('14'), Decimal('30')), Decimal('46.7'))
        self.assertEqual(_percentual(Decimal('1'), Decimal('0')), Decimal('0'))
