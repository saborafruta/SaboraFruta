from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import RequestFactory, SimpleTestCase

from apps.core.views.notificacoes import NotificacaoAbrirView


class NotificacaoAbrirViewTests(SimpleTestCase):
    @patch('apps.core.views.notificacoes.NotificacaoLeitura.objects.get_or_create')
    @patch('apps.core.views.notificacoes.get_object_or_404')
    def test_marca_leitura_por_ids_para_aceitar_usuario_de_outro_banco(
        self,
        get_object_or_404,
        get_or_create,
    ):
        notificacao = SimpleNamespace(pk=31, url='/estoque/')
        get_object_or_404.return_value = notificacao
        request = RequestFactory().get('/notificacoes/31/abrir/')
        request.user = SimpleNamespace(is_authenticated=True, pk=17)
        request.filial_ativa = SimpleNamespace(pk=9)

        response = NotificacaoAbrirView.as_view()(request, pk=31)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/estoque/')
        get_or_create.assert_called_once_with(
            notificacao_id=31,
            usuario_id=17,
        )

