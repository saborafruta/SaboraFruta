from unittest.mock import patch

from django.test import SimpleTestCase

from apps.core.models import Usuario


class UsuarioFotoTests(SimpleTestCase):
    def test_atualizar_last_login_nao_tenta_otimizar_foto(self):
        usuario = Usuario(email='foto-ausente@teste.local', nome='Foto Ausente')
        usuario.foto.name = 'usuarios/fotos/arquivo-que-nao-existe.jpg'

        with patch.object(Usuario, '_otimizar_foto') as otimizar, \
                patch('django.db.models.Model.save'):
            usuario.save(update_fields=['last_login'])

        otimizar.assert_not_called()

