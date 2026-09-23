from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.produtos.services.codigo_balanca_service import (
    CodigoBalancaInvalido,
    calcular_leitura_etiqueta,
    decodificar_ean_balanca,
)
from apps.produtos.services.codigo_barras_service import calcular_digito_verificador_ean13


def montar_ean(base):
    return base + calcular_digito_verificador_ean13(base)


class CodigoBalancaServiceTests(SimpleTestCase):
    def test_decodifica_plu_e_preco_total_da_etiqueta_mgv7(self):
        dados = decodificar_ean_balanca(montar_ean('20' + '00001' + '01229'))
        produto = SimpleNamespace(preco_venda=Decimal('18.90'))

        leitura = calcular_leitura_etiqueta(produto, dados, conteudo='preco_total')

        self.assertEqual(dados.plu, '1')
        self.assertEqual(dados.valor_bruto, 1229)
        self.assertEqual(leitura.quantidade, Decimal('0.650'))
        self.assertEqual(leitura.valor_total, Decimal('12.29'))

    def test_decodifica_peso_em_gramas_quando_configurado(self):
        dados = decodificar_ean_balanca(montar_ean('20' + '00003' + '00650'))
        produto = SimpleNamespace(preco_venda=Decimal('4.00'))

        leitura = calcular_leitura_etiqueta(produto, dados, conteudo='peso')

        self.assertEqual(dados.plu, '3')
        self.assertEqual(leitura.quantidade, Decimal('0.650'))
        self.assertEqual(leitura.valor_total, Decimal('2.60'))

    def test_rejeita_ean_com_digito_verificador_invalido(self):
        with self.assertRaises(CodigoBalancaInvalido):
            decodificar_ean_balanca('200001012290')
