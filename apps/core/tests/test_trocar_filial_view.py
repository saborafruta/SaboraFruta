from unittest.mock import Mock, patch

from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from apps.core.services.exceptions import DadosInvalidosError
from apps.core.views.auth import TrocarFilialView


class TrocarFilialViewTests(SimpleTestCase):
    def test_falha_na_troca_volta_para_selecao_em_vez_do_tenant_anterior(self):
        request = RequestFactory().get(
            '/auth/trocar-filial/2/',
            HTTP_REFERER='https://ited.app.br/auth/selecionar-filial/',
        )
        request.user = Mock(is_authenticated=True)

        with (
            patch(
                'apps.core.views.auth.AuthService.trocar_filial',
                side_effect=DadosInvalidosError('Banco indisponivel.'),
            ),
            patch('apps.core.views.auth.messages.error'),
        ):
            response = TrocarFilialView().get(request, 2)

        self.assertRedirects(
            response,
            reverse('core:selecionar-filial'),
            fetch_redirect_response=False,
        )
