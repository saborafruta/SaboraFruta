from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.core.management.commands.migrate_tenant_databases import Command
from apps.core.models import EmpresaBanco


class TenantMigrationRecoveryTests(SimpleTestCase):
    def setUp(self):
        self.banco = SimpleNamespace(pk=7, db_alias='empresa_recuperavel')

    def _run_handle(self, retry_errors):
        command = Command()
        queryset = Mock()
        queryset.filter.return_value = queryset
        queryset.select_related.return_value = queryset
        queryset.order_by.return_value = [self.banco]

        with (
            patch.object(EmpresaBanco.objects, 'using', return_value=queryset),
            patch.object(command, '_migrate_database') as migrate,
        ):
            command.handle(continue_on_error=True, retry_errors=retry_errors)

        return queryset, migrate

    def test_retry_errors_inclui_banco_em_erro(self):
        queryset, migrate = self._run_handle(retry_errors=True)

        queryset.filter.assert_called_once_with(
            ativo=True,
            status__in=[EmpresaBanco.Status.ATIVO, EmpresaBanco.Status.ERRO],
            empresa__ativo=True,
        )
        migrate.assert_called_once_with(self.banco)

    def test_sem_retry_errors_preserva_comportamento_padrao(self):
        queryset, migrate = self._run_handle(retry_errors=False)

        queryset.filter.assert_called_once_with(
            ativo=True,
            status__in=[EmpresaBanco.Status.ATIVO],
            empresa__ativo=True,
        )
        migrate.assert_called_once_with(self.banco)

    def test_migracao_bem_sucedida_reativa_banco(self):
        command = Command()
        connection = Mock(vendor='sqlite')
        manager = Mock()

        with (
            patch(
                'apps.core.management.commands.migrate_tenant_databases.'
                'register_tenant_database',
                return_value=True,
            ),
            patch(
                'apps.core.management.commands.migrate_tenant_databases.connections',
                {self.banco.db_alias: connection},
            ),
            patch(
                'apps.core.management.commands.migrate_tenant_databases.call_command'
            ),
            patch.object(EmpresaBanco.objects, 'using', return_value=manager),
        ):
            command._migrate_database(self.banco)

        manager.filter.assert_called_once_with(pk=self.banco.pk)
        update_kwargs = manager.filter.return_value.update.call_args.kwargs
        self.assertEqual(update_kwargs['status'], EmpresaBanco.Status.ATIVO)
        self.assertEqual(update_kwargs['ultimo_erro'], '')
        connection.close.assert_called_once_with()
