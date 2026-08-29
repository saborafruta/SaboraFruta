from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.pdv.views.pdv import _produto_imagem_payload


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
