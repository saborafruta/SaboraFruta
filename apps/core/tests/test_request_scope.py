from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.core.models import Usuario
from apps.core.services.request_scope import usuario_operacional


class UsuarioOperacionalTests(SimpleTestCase):
    def test_resolve_superusuario_central_pelo_email_no_tenant(self):
        usuario_tenant = Mock()
        usuario_central = SimpleNamespace(
            is_authenticated=True,
            email="ITED@ITED.COM",
            _state=SimpleNamespace(db="default"),
        )
        request = SimpleNamespace(user=usuario_central)
        queryset = Mock()
        queryset.filter.return_value.first.return_value = usuario_tenant

        with (
            patch(
                "apps.core.tenant_context.get_current_database_alias",
                return_value="tenant_ited",
            ),
            patch.object(Usuario.objects, "using", return_value=queryset) as using,
        ):
            resolvido = usuario_operacional(request, obrigatorio=True)

        self.assertIs(resolvido, usuario_tenant)
        using.assert_called_once_with("tenant_ited")
        queryset.filter.assert_called_once_with(
            email__iexact="ITED@ITED.COM",
            ativo=True,
        )

    def test_preserva_usuario_que_ja_pertence_ao_banco_operacional(self):
        usuario = SimpleNamespace(
            is_authenticated=True,
            email="operador@ited.com",
            _state=SimpleNamespace(db="tenant_ited"),
        )

        with patch(
            "apps.core.tenant_context.get_current_database_alias",
            return_value="tenant_ited",
        ):
            resolvido = usuario_operacional(SimpleNamespace(user=usuario), obrigatorio=True)

        self.assertIs(resolvido, usuario)
