import json
import zipfile
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from uuid import UUID

from django.test import SimpleTestCase, TestCase

from apps.core.models import Empresa, EmpresaBanco
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

    def test_literal_sql_e_csv_suportam_tipos_de_backup(self):
        uuid = UUID('12345678-1234-5678-1234-567812345678')

        self.assertEqual(
            EmpresaBancoService._sql_literal({'valor': Decimal('1.50'), 'itens': [1, 2]}),
            "'{\"valor\":\"1.50\",\"itens\":[1,2]}'",
        )
        self.assertEqual(EmpresaBancoService._sql_literal(uuid), f"'{uuid}'")
        self.assertEqual(EmpresaBancoService._csv_value(uuid), str(uuid))

    @patch.object(EmpresaBancoService, 'gerar_backup_completo_persistente')
    def test_exclusao_sem_servico_railway_nao_remove_so_o_cadastro(self, backup):
        backup.return_value = ('backup.zip', Path('backup.zip'))
        banco = SimpleNamespace(
            railway_database_service_id='', provisionamento_modo='manual',
            delete=Mock(),
        )

        with self.assertRaisesRegex(RuntimeError, 'exclusão automática foi bloqueada'):
            EmpresaBancoService.excluir_banco_com_backup(banco)

        banco.delete.assert_not_called()

    @patch('apps.core.services.empresa_banco_service.RailwayProvisioner.delete_postgres')
    @patch.object(EmpresaBancoService, 'gerar_backup_completo_persistente')
    def test_exclusao_importada_remove_servico_railway_antes_do_cadastro(
        self, backup, delete_postgres,
    ):
        backup.return_value = ('backup.zip', Path('backup.zip'))
        delete_postgres.return_value = {'service_id': 'tenant-id'}
        banco = SimpleNamespace(
            railway_database_service_id='tenant-id', provisionamento_modo='manual',
            delete=Mock(),
        )

        EmpresaBancoService.excluir_banco_com_backup(banco)

        delete_postgres.assert_called_once_with(banco)
        banco.delete.assert_called_once_with(using='default')


class EmpresaBancoBackupTests(TestCase):
    def test_backup_completo_inclui_sql_csvs_e_manifesto(self):
        empresa = Empresa.objects.create(
            razao_social='Empresa Backup LTDA',
            nome_fantasia='Empresa Backup',
            cnpj='33444555000166',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        banco = SimpleNamespace(
            slug='empresa-backup', db_alias='default', empresa=empresa,
        )

        with TemporaryDirectory() as tmpdir:
            with patch(
                'apps.core.services.empresa_banco_service.register_tenant_database',
                return_value=True,
            ):
                filename, archive_path = EmpresaBancoService.gerar_backup_completo_em_arquivo(
                    banco, tmpdir,
                )
            with zipfile.ZipFile(archive_path) as archive:
                nomes = archive.namelist()
                manifesto = json.loads(archive.read('manifesto.json'))
                tabela = empresa._meta.db_table
                csv_empresa = archive.read(f'csv/{tabela}.csv').decode('utf-8-sig')

            self.assertEqual(Path(filename).suffix, '.zip')
            self.assertTrue(any(nome.endswith('.sql') for nome in nomes))
            self.assertIn(f'csv/{tabela}.csv', nomes)
            self.assertIn('manifesto.json', nomes)
            self.assertEqual(manifesto['formato'], 'ited-backup-completo-v1')
            self.assertIn(empresa.razao_social, csv_empresa)
            self.assertFalse(any(Path(tmpdir).glob('*.sql')))
