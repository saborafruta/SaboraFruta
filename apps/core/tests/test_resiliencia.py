from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse


class ResilienciaTests(TestCase):
    def test_health_check_responde_ok(self):
        with TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=Path(media_root)):
            response = self.client.get(reverse('health'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertTrue(payload['checks']['database'])
        self.assertTrue(payload['checks']['media_root'])
        self.assertFalse(payload['integrations']['orla_widget'])

    def test_health_check_confirma_widget_orla_no_host_autorizado(self):
        with TemporaryDirectory() as media_root, self.settings(
            MEDIA_ROOT=Path(media_root),
            ORLA_WIDGET_ENABLED=True,
            ORLA_WIDGET_ALLOWED_HOSTS=['testserver'],
        ):
            response = self.client.get(reverse('health'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['integrations']['orla_widget'])

    def test_backup_database_gera_arquivo(self):
        with TemporaryDirectory() as tmpdir:
            call_command('backup_database', output_dir=tmpdir, verbosity=0)

            arquivos = list(Path(tmpdir).glob('erp_inoovated_db_*.jsonl.gz'))
            self.assertEqual(len(arquivos), 1)
            self.assertGreater(arquivos[0].stat().st_size, 0)

    def test_backup_media_gera_tar_gz(self):
        with TemporaryDirectory() as media_root, TemporaryDirectory() as output_dir:
            Path(media_root, 'arquivo.txt').write_text('ok', encoding='utf-8')

            call_command(
                'backup_media',
                media_root=media_root,
                output_dir=output_dir,
                verbosity=0,
            )

            arquivos = list(Path(output_dir).glob('erp_inoovated_media_*.tar.gz'))
            self.assertEqual(len(arquivos), 1)
            self.assertGreater(arquivos[0].stat().st_size, 0)
