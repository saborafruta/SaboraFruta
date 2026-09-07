from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.test import SimpleTestCase

from apps.core.models import EmpresaBanco
from apps.core.services.empresa_banco_service import EmpresaBancoService
from apps.core.tenant_context import get_current_tenant_db


class EmpresaBancoServiceTests(SimpleTestCase):
    def test_migrations_executam_no_contexto_do_tenant(self):
        banco = SimpleNamespace(
            db_alias='empresa_teste',
            empresa=SimpleNamespace(pk=4),
            status=EmpresaBanco.Status.CONFIGURADO,
            ultima_migracao_em=None,
            ultimo_erro='',
            save=Mock(),
        )
        contexts = []
        fake_connection = MagicMock()
        fake_connections = MagicMock()
        fake_connections.__getitem__.return_value = fake_connection

        def record_context(*args, **kwargs):
            contexts.append(get_current_tenant_db())

        with (
            patch(
                'apps.core.services.empresa_banco_service.register_tenant_database',
                return_value=True,
            ),
            patch(
                'apps.core.services.empresa_banco_service.call_command',
                side_effect=record_context,
            ),
            patch(
                'apps.core.services.empresa_banco_service.TenantBootstrapService.sincronizar_empresa',
                return_value={'usuarios': 0},
            ) as synchronize,
            patch(
                'apps.core.services.empresa_banco_service.connections',
                fake_connections,
            ),
        ):
            ok, message = EmpresaBancoService.migrar_banco(banco)

        self.assertTrue(ok)
        self.assertEqual(message, 'Banco ativo; 0 usuários sincronizados.')
        self.assertEqual(contexts, ['empresa_teste'])
        synchronize.assert_called_once_with(banco.empresa, 'empresa_teste')
        self.assertEqual(banco.status, EmpresaBanco.Status.ATIVO)
        fake_connection.close.assert_called_once()
