from unittest.mock import patch

from django.test import SimpleTestCase

from apps.core.storage import TenantS3Storage
from apps.core.tenant_context import tenant_db


class TenantS3StorageTests(SimpleTestCase):
    def setUp(self):
        self.storage = TenantS3Storage(bucket_name='test-media-bucket')

    def test_uploads_are_namespaced_to_active_tenant(self):
        with tenant_db('empresa_saborafruta_14004764000240'):
            key = self.storage._namespace_upload('filiais/imagens/logo.png')
            self.assertTrue(key.startswith(
                self.storage._tenant_prefix('empresa_saborafruta_14004764000240'),
            ))
            self.assertTrue(key.endswith('filiais/imagens/logo.png'))

    def test_available_upload_name_includes_tenant_prefix(self):
        with tenant_db('empresa_saborafruta_14004764000240'), patch.object(
            self.storage, 'exists', return_value=False,
        ):
            self.assertEqual(
                self.storage.get_available_name('filiais/imagens/logo.png'),
                self.storage._tenant_prefix('empresa_saborafruta_14004764000240')
                + 'filiais/imagens/logo.png',
            )

    def test_long_upload_path_is_shortened_to_fit_default_filefield_limit(self):
        with tenant_db('empresa_saborafruta_14004764000240'):
            key = self.storage._namespace_upload(
                f'usuarios/fotos/{"a" * 140}.jpg', max_length=100,
            )
        self.assertLessEqual(len(key), 100)
        self.assertTrue(key.endswith('.jpg'))

    def test_central_upload_keeps_existing_key_layout(self):
        with tenant_db(None):
            self.assertEqual(
                self.storage._namespace_upload('central/logo.png'),
                'central/logo.png',
            )

    def test_tenant_cannot_read_another_tenants_namespaced_key(self):
        with tenant_db('empresa_saborafruta_14004764000240'):
            with self.assertRaises(FileNotFoundError):
                self.storage._deny_other_tenant_key(
                    'tenant-media/empresa_ited_06722483000114/usuarios/fotos/a.jpg',
                )

    def test_foreign_tenant_url_becomes_not_found_without_signing(self):
        with tenant_db('empresa_saborafruta_14004764000240'), patch(
            'apps.core.storage.S3Storage.url',
        ) as parent_url:
            url = self.storage.url(
                'tenant-media/empresa_ited_06722483000114/usuarios/fotos/a.jpg',
            )
        self.assertEqual(url, '/media/__tenant_media_not_found__')
        parent_url.assert_not_called()

    def test_tenant_cannot_write_to_another_tenants_prefix(self):
        with tenant_db('empresa_saborafruta_14004764000240'):
            with self.assertRaises(ValueError):
                self.storage._namespace_upload(
                    'tenant-media/empresa_ited_06722483000114/logo.png',
                )

    def test_tenant_cannot_delete_a_legacy_shared_key(self):
        with tenant_db('empresa_saborafruta_14004764000240'), patch(
            'apps.core.storage.S3Storage.delete',
        ) as parent_delete:
            self.storage.delete('usuarios/fotos/legacy.jpg')
            parent_delete.assert_not_called()
