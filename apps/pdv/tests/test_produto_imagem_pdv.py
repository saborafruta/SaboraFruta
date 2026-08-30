from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.pdv.views.pdv import _finalizar_ofertas, _produto_imagem_payload


class ProdutoImagemPDVTests(SimpleTestCase):
    def test_payload_com_foto_entrega_miniatura_zoom_e_upload(self):
        produto = SimpleNamespace(pk=42, foto_url='produtos/imagens/cafe.jpg')

        payload = _produto_imagem_payload(produto)

        self.assertTrue(payload['tem_foto'])
        self.assertEqual(payload['foto_thumb_url'], '/produtos/42/foto/?v=thumb')
        self.assertEqual(payload['foto_url'], '/produtos/42/foto/?v=zoom')
        self.assertEqual(payload['foto_update_url'], '/produtos/42/imagem/')

    def test_payload_sem_foto_mantem_cadastro_disponivel(self):
        produto = SimpleNamespace(pk=42, foto_url='')

        payload = _produto_imagem_payload(produto)

        self.assertFalse(payload['tem_foto'])
        self.assertEqual(payload['foto_thumb_url'], '')
        self.assertEqual(payload['foto_url'], '')
        self.assertEqual(payload['foto_update_url'], '/produtos/42/imagem/')

    def test_template_separa_clique_da_foto_do_clique_de_venda(self):
        template = (
            Path(__file__).resolve().parents[1] / 'templates' / 'pdv' / 'home.html'
        ).read_text(encoding='utf-8')

        self.assertIn('@click.stop="abrirFotoProduto(p)"', template)
        self.assertIn('@click.stop="abrirFotoProduto(item)"', template)
        self.assertIn('class="cart-item-photo"', template)
        self.assertIn("foto_thumb_url: produto.foto_thumb_url||''", template)
        self.assertIn("x-text=\"modalFoto.url ? 'Trocar foto' : 'Cadastrar foto'\"", template)
        self.assertIn('?v=zoom', _produto_imagem_payload(
            SimpleNamespace(pk=1, foto_url='foto.jpg'),
        )['foto_url'])

    def test_modal_identifica_tipo_campanha_validade_e_brinde(self):
        template = (
            Path(__file__).resolve().parents[1] / 'templates' / 'pdv' / 'home.html'
        ).read_text(encoding='utf-8')

        self.assertIn('x-text="p.tag || p.tipo"', template)
        self.assertIn('class="preco-campanha-badge"', template)
        self.assertIn('x-text="validadeModalOferta(p)"', template)
        self.assertIn("percentualOferta(p)+'% de desconto'", template)
        self.assertNotIn('As opções estão ordenadas pela maior economia.', template)
        self.assertNotIn('>Menor preço</span>', template)
        self.assertIn('@click="abrirPrecoItem(item, true)"', template)
        self.assertIn("'Brinde incluído: '+item.oferta_brindes.join(', ')", template)

    def test_ofertas_sao_ordenadas_pelo_menor_preco_unitario(self):
        ofertas = _finalizar_ofertas([
            {'tipo': 'normal', 'preco': 10, 'total': 10, 'preco_referencia': 10, 'quantidade': 1},
            {'tipo': 'combo', 'preco': 8, 'total': 24, 'preco_referencia': 30, 'quantidade': 3},
            {'tipo': 'brinde', 'preco': 10, 'total': 20, 'preco_referencia': 30, 'quantidade': 2},
        ])

        self.assertEqual([item['tipo'] for item in ofertas], ['combo', 'brinde', 'normal'])
        self.assertTrue(ofertas[0]['melhor'])
        self.assertEqual(ofertas[0]['economia'], 6.0)
        self.assertEqual(sum(item['melhor'] for item in ofertas), 1)
