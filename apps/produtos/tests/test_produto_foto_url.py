from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from apps.produtos.models import Produto
from apps.produtos.views.produto import _gravar_imagem_produto


class ProdutoFotoUrlTests(SimpleTestCase):
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
