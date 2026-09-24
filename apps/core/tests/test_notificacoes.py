from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import RequestFactory, SimpleTestCase

from apps.core.views.notificacoes import NotificacaoAbrirView, NotificacaoMarcarLidaView


class NotificacaoAbrirViewTests(SimpleTestCase):
    def test_alerta_de_ajuste_pulsa_vermelho_ate_confirmar_ciencia(self):
        template = Path('apps/core/templates/core/inicio.html').read_text(encoding='utf-8')
        estilos = Path('apps/core/static/core/css/inicio.css').read_text(encoding='utf-8')

        self.assertIn("notificacao.tipo == 'moda_cliente_ajuste'", template)
        self.assertIn('is-critical', template)
        self.assertIn('notification-critical-pulse 1.5s ease-in-out infinite', estilos)
        self.assertIn('.notification-row.is-critical .notification-indicator', estilos)

    @patch('apps.core.views.notificacoes.NotificacaoLeitura.objects.get_or_create')
    @patch('apps.core.views.notificacoes.get_object_or_404')
    def test_abrir_notificacao_nao_marca_como_lida_sem_confirmacao(
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
        get_or_create.assert_not_called()

    @patch('apps.core.views.notificacoes.NotificacaoLeitura.objects.get_or_create')
    @patch('apps.core.views.notificacoes.get_object_or_404')
    def test_estou_ciente_marca_leitura_por_ids_para_usuario_do_tenant(
        self,
        get_object_or_404,
        get_or_create,
    ):
        notificacao = SimpleNamespace(pk=31)
        get_object_or_404.return_value = notificacao
        request = RequestFactory().post('/notificacoes/31/marcar-lida/')
        request.user = SimpleNamespace(is_authenticated=True, pk=17)
        request.filial_ativa = SimpleNamespace(pk=9)

        response = NotificacaoMarcarLidaView.as_view()(request, pk=31)

        self.assertEqual(response.status_code, 200)
        get_or_create.assert_called_once_with(
            notificacao_id=31,
            usuario_id=17,
        )

