from io import BytesIO
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase
from PIL import Image

from apps.core.models import Usuario
from apps.core.views.auth import atualizar_minha_foto


class UsuarioFotoTests(SimpleTestCase):
    def test_otimizacao_aplica_orientacao_exif_antes_do_recorte(self):
        imagem = Image.new('RGB', (100, 100))
        imagem.paste('red', (0, 0, 50, 50))
        imagem.paste('green', (50, 0, 100, 50))
        imagem.paste('blue', (0, 50, 50, 100))
        imagem.paste('yellow', (50, 50, 100, 100))
        exif = Image.Exif()
        exif[274] = 6
        origem = BytesIO()
        imagem.save(origem, format='JPEG', quality=100, exif=exif)

        usuario = Usuario(email='foto-exif@teste.local', nome='Foto EXIF')
        usuario.foto = SimpleUploadedFile('perfil.jpg', origem.getvalue(), content_type='image/jpeg')
        usuario._otimizar_foto()

        usuario.foto.file.seek(0)
        resultado = Image.open(usuario.foto.file)
        canto_superior_esquerdo = resultado.getpixel((64, 64))

        self.assertEqual(resultado.size, (512, 512))
        self.assertIsNone(resultado.getexif().get(274))
        self.assertGreater(canto_superior_esquerdo[2], canto_superior_esquerdo[0])

    def test_atualizar_last_login_nao_tenta_otimizar_foto(self):
        usuario = Usuario(email='foto-ausente@teste.local', nome='Foto Ausente')
        usuario.foto.name = 'usuarios/fotos/arquivo-que-nao-existe.jpg'

        with patch.object(Usuario, '_otimizar_foto') as otimizar, \
                patch('django.db.models.Model.save'):
            usuario.save(update_fields=['last_login'])

        otimizar.assert_not_called()

    @patch('apps.core.views.auth.messages.success')
    @patch('apps.core.views.auth._usuario_central')
    def test_foto_de_sessao_legada_atualiza_usuario_central_e_local(
        self,
        usuario_central,
        _mensagem,
    ):
        central = Usuario(email='foto-global@teste.local', nome='Foto Global')
        central._state.db = 'default'
        central.save = Mock()
        usuario_central.return_value = central

        local = Usuario(email=central.email, nome=central.nome)
        local._state.db = 'empresa_teste'
        local.save = Mock()

        imagem = SimpleUploadedFile(
            'perfil.png',
            b'conteudo-da-imagem',
            content_type='image/png',
        )
        request = RequestFactory().post(
            '/auth/minha-foto/',
            {'foto': imagem},
        )
        request.user = local
        request.session = {}

        response = atualizar_minha_foto(request)

        self.assertEqual(response.status_code, 302)
        central.save.assert_called_once_with(
            using='default',
            update_fields=['foto', 'updated_at'],
        )
        local.save.assert_called_once_with(
            using='empresa_teste',
            update_fields=['foto', 'updated_at'],
        )
        self.assertEqual(local.foto.name, central.foto.name)

