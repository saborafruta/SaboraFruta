from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.pdv.services.oferta_contexto_service import (
    aplicar_contexto_oferta,
    contexto_oferta_do_payload,
)


class OfertaContextoServiceTests(SimpleTestCase):
    def test_preserva_regra_e_apresentacao_da_campanha(self):
        contexto = contexto_oferta_do_payload({
            'oferta_tipo': 'brinde',
            'oferta_tag': 'COMPRE E GANHE',
            'oferta_nome': 'Campanha de aniversário',
            'brinde_id': 17,
            'oferta_brindes': ['1x Camiseta'],
            'oferta_brindes_estoque': [{
                'produto_id': 9, 'nome': 'Camiseta', 'quantidade': 1,
                'estoque_disponivel': 4, 'estoque_suficiente': True,
            }],
            '_quantidadeMinOferta': 2,
            '_ofertaSelecionada': True,
            '_precoOriginal': 10,
            '_precoTabela': 15,
            'oferta_componentes_estoque': [{'produto_id': 9, 'quantidade': 2}],
        })

        self.assertEqual(contexto['brinde_id'], 17)
        self.assertEqual(contexto['quantidade_minima_oferta'], 2)
        self.assertTrue(contexto['oferta_selecionada'])

        item = SimpleNamespace(
            oferta_contexto=contexto,
            preco_origem='brinde',
            preco_origem_detalhe='Campanha de aniversário',
        )
        payload = aplicar_contexto_oferta({}, item)
        self.assertEqual(payload['oferta_nome'], 'Campanha de aniversário')
        self.assertEqual(payload['_quantidadeMinOferta'], 2)
        self.assertTrue(payload['_ofertaSelecionada'])
        self.assertEqual(payload['preco_tabela'], 15)
        self.assertEqual(payload['preco_original'], 10)
        self.assertEqual(payload['oferta_componentes_estoque'], [{'produto_id': 9, 'quantidade': 2}])

    def test_template_exibe_escolha_sem_brinde_e_saldo_do_presente(self):
        template = (
            Path(__file__).resolve().parents[1] / 'templates' / 'pdv' / 'home.html'
        ).read_text(encoding='utf-8')

        self.assertIn('SEM BRINDE', template)
        self.assertIn("'Estoque: '+fmtQtd(brinde.estoque_disponivel)", template)
        self.assertIn('itens: this.venda.itens', template)
        self.assertIn('itens:this.venda.itens', template)
        self.assertIn('const itensPrecificar = this.venda.itens.filter', template)
