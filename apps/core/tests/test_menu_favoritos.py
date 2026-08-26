import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from apps.core.models import Usuario
from apps.core.views.menu_favoritos import MenuFavoritosView, normalizar_caminho_favorito


class MenuFavoritosViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.usuario = SimpleNamespace(pk=7, menu_favoritos=[], save=Mock())

    def _post(self, caminho, favorito, *, autenticado=True):
        request = self.factory.post(
            reverse('core:menu-favoritos'),
            data=json.dumps({'caminho': caminho, 'favorito': favorito}),
            content_type='application/json',
        )
        request.user = (
            SimpleNamespace(is_authenticated=True, pk=self.usuario.pk)
            if autenticado else AnonymousUser()
        )
        selecionado = Mock()
        selecionado.get.return_value = self.usuario
        with patch(
            'apps.core.views.menu_favoritos.Usuario.objects.select_for_update',
            return_value=selecionado,
        ), patch(
            'apps.core.views.menu_favoritos.transaction.atomic',
            return_value=nullcontext(),
        ):
            return MenuFavoritosView.as_view()(request)

    def test_adiciona_e_remove_favorito(self):
        response = self._post('/financeiro/pagar/?status=pendente', True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.usuario.menu_favoritos, ['/financeiro/pagar/'])

        response = self._post('/financeiro/pagar/', False)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.usuario.menu_favoritos, [])
        self.assertEqual(self.usuario.save.call_count, 2)

    def test_atualiza_somente_usuario_selecionado(self):
        outro_usuario = SimpleNamespace(menu_favoritos=['/core/dashboard/'])

        self._post('/estoque/ajuste-rapido/', True)

        self.assertEqual(self.usuario.menu_favoritos, ['/estoque/ajuste-rapido/'])
        self.assertEqual(outro_usuario.menu_favoritos, ['/core/dashboard/'])

    def test_rejeita_url_externa_e_logout(self):
        externo = self._post('https://exemplo.com/financeiro/', True)
        logout = self._post('/auth/logout/', True)

        self.assertEqual(externo.status_code, 400)
        self.assertEqual(logout.status_code, 400)
        self.usuario.save.assert_not_called()

    def test_exige_autenticacao(self):
        response = self._post('/financeiro/', True, autenticado=False)

        self.assertEqual(response.status_code, 302)
        self.usuario.save.assert_not_called()

    def test_normaliza_query_e_rejeita_caminho_relativo(self):
        self.assertEqual(
            normalizar_caminho_favorito('/financeiro/pagar/?status=pendente'),
            '/financeiro/pagar/',
        )
        self.assertIsNone(normalizar_caminho_favorito('financeiro/pagar/'))

    def test_modelo_inicia_sem_favoritos(self):
        self.assertEqual(Usuario().menu_favoritos, [])


class MenuFavoritosTemplateTests(SimpleTestCase):
    def test_sidebar_carrega_favoritos_no_desktop_e_celular(self):
        raiz = Path(__file__).resolve().parents[1]
        template = (raiz / 'templates' / 'core' / '_sidebar.html').read_text(encoding='utf-8')

        self.assertEqual(template.count('sidebar-favorites-nav'), 2)
        self.assertIn('core/js/sidebar_favorites.js', template)
        self.assertIn('request.user.menu_favoritos', template)
        self.assertIn('@media (hover: hover) and (pointer: fine)', template)
        self.assertIn('.sidebar-favorite-toggle:not(.is-favorite)', template)
        self.assertIn('.sidebar-mobile .sidebar-favorite-toggle', template)
        self.assertIn('class="sidebar-mobile fixed inset-y-0', template)
