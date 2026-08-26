import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase
from PIL import Image

from apps.produtos.models import Produto
from apps.produtos.views.produto import ProdutoImagemView, _gravar_imagem_produto


class ProdutoFotoUrlTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_renova_url_assinada_antiga_do_bucket(self):
        produto = Produto(
            foto_url=(
                'https://bucket.example/produtos/imagens/camisa.jpg'
                '?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=3600'
            )
        )

        with patch(
            'apps.produtos.models.produto.default_storage.url',
            return_value='https://bucket.example/produtos/imagens/camisa.jpg?assinatura=nova',
        ) as storage_url:
            resolvida = produto.foto_url_resolvida

        self.assertEqual(
            resolvida,
            'https://bucket.example/produtos/imagens/camisa.jpg?assinatura=nova',
        )
        storage_url.assert_called_once_with('produtos/imagens/camisa.jpg')

    def test_resolve_chave_estavel_salva_por_novos_uploads(self):
        produto = Produto(foto_url='produtos/imagens/camisa.jpg')

        with patch(
            'apps.produtos.models.produto.default_storage.url',
            return_value='https://bucket.example/camisa.jpg?assinatura=atual',
        ):
            self.assertEqual(
                produto.foto_url_resolvida,
                'https://bucket.example/camisa.jpg?assinatura=atual',
            )

    def test_preserva_url_publica_externa(self):
        produto = Produto(foto_url='https://cdn.example/camisa.jpg')

        with patch('apps.produtos.models.produto.default_storage.url') as storage_url:
            self.assertEqual(produto.foto_url_resolvida, produto.foto_url)

        storage_url.assert_not_called()

    @patch(
        'apps.produtos.views.produto.default_storage.save',
        return_value='produtos/imagens/nova.jpg',
    )
    def test_novo_upload_salva_chave_estavel_em_vez_de_url_temporaria(self, storage_save):
        produto = Produto()
        imagem = SimpleUploadedFile('camisa.jpg', b'conteudo', content_type='image/jpeg')

        _gravar_imagem_produto(produto, imagem)

        self.assertEqual(produto.foto_url, 'produtos/imagens/nova.jpg')
        storage_save.assert_called_once()

    @patch.object(ProdutoImagemView, '_arquivo_otimizado')
    @patch('apps.produtos.views.produto.get_object_or_404')
    def test_endpoint_estavel_entrega_imagem_otimizada_sem_cache(self, get_object, otimizar):
        get_object.return_value = SimpleNamespace()
        otimizar.return_value = io.BytesIO(b'imagem-otimizada')

        response = ProdutoImagemView.as_view()(self.factory.get('/produtos/1/foto/'), pk=1)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertIn('no-store', response['Cache-Control'])
        otimizar.assert_called_once_with(get_object.return_value, 'zoom')

    @patch('apps.produtos.views.produto.get_object_or_404')
    def test_endpoint_estavel_retorna_404_quando_produto_nao_tem_foto(self, get_object):
        get_object.return_value = Produto(foto_url='')

        with self.assertRaises(Http404):
            ProdutoImagemView.as_view()(self.factory.get('/produtos/1/foto/'), pk=1)

    def test_variantes_limitam_pixels_decodificados_no_celular(self):
        original = io.BytesIO()
        Image.new('RGB', (4032, 3024), '#1d4ed8').save(original, format='JPEG')
        original.seek(0)

        miniatura = ProdutoImagemView._converter(original, 'thumb')
        with Image.open(miniatura) as imagem_miniatura:
            self.assertLessEqual(max(imagem_miniatura.size), 320)

        original.seek(0)
        zoom = ProdutoImagemView._converter(original, 'zoom')
        with Image.open(zoom) as imagem_zoom:
            self.assertLessEqual(max(imagem_zoom.size), 1400)

    def test_falha_da_miniatura_nao_apaga_url_do_zoom(self):
        template = (
            Path(__file__).resolve().parents[3]
            / 'apps/estoque/templates/estoque/estoque/ajuste_rapido.html'
        ).read_text(encoding='utf-8')

        self.assertIn('data-photo-thumbnail', template)
        self.assertNotIn("button.dataset.photoUrl=''", template)
