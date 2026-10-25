"""S3 media storage with an isolated key prefix for each tenant database."""
import hashlib
from pathlib import PurePosixPath

from storages.backends.s3 import S3Storage

from apps.core.tenant_context import get_current_tenant_db


TENANT_MEDIA_PREFIX = 'tenant-media'


class TenantS3Storage(S3Storage):
    """Keep tenant uploads in separate S3 prefixes while preserving old keys."""

    @staticmethod
    def _tenant_prefix(alias):
        tenant_key = hashlib.sha256(alias.encode('utf-8')).hexdigest()[:24]
        return f'{TENANT_MEDIA_PREFIX}/{tenant_key}/'

    @classmethod
    def migrated_name(cls, alias, old_name):
        extension = PurePosixPath(old_name).suffix[:16]
        digest = hashlib.sha256(old_name.encode('utf-8')).hexdigest()[:32]
        return f'{cls._tenant_prefix(alias)}objects/{digest}{extension}'

    @classmethod
    def isolated_name(cls, alias, name):
        """Return an existing tenant key unchanged, or a stable migration key."""
        if name.startswith(cls._tenant_prefix(alias)):
            return name
        return cls.migrated_name(alias, name)

    def _namespace_upload(self, name, max_length=None):
        alias = get_current_tenant_db()
        if not alias:
            return name
        prefix = self._tenant_prefix(alias)
        if name.startswith(prefix):
            return name
        if name.startswith(f'{TENANT_MEDIA_PREFIX}/'):
            raise ValueError('Não é permitido gravar mídia no espaço de outro tenant.')
        key = f'{prefix}{name.lstrip("/")}'
        if max_length and len(key) > max_length:
            extension = PurePosixPath(name).suffix[:16]
            digest = hashlib.sha256(name.encode('utf-8')).hexdigest()[:32]
            key = f'{prefix}objects/{digest}{extension}'
        if max_length and len(key) > max_length:
            raise ValueError('O limite de tamanho do caminho de mídia é insuficiente.')
        return key

    def get_available_name(self, name, max_length=None):
        return super().get_available_name(
            self._namespace_upload(name, max_length=max_length), max_length=max_length,
        )

    def _deny_other_tenant_key(self, name):
        alias = get_current_tenant_db()
        if not alias or not name.startswith(f'{TENANT_MEDIA_PREFIX}/'):
            return
        if not name.startswith(self._tenant_prefix(alias)):
            # A missing key response avoids disclosing whether another tenant's
            # object exists, while still preventing its signed URL generation.
            raise FileNotFoundError('Mídia não encontrada.')

    def open(self, name, mode='rb'):
        self._deny_other_tenant_key(name)
        return super().open(name, mode)

    def url(self, name, parameters=None, expire=None, http_method=None):
        try:
            self._deny_other_tenant_key(name)
        except FileNotFoundError:
            return '/media/__tenant_media_not_found__'
        return super().url(
            name, parameters=parameters, expire=expire, http_method=http_method,
        )

    def exists(self, name):
        try:
            self._deny_other_tenant_key(name)
        except FileNotFoundError:
            return False
        return super().exists(name)

    def delete(self, name):
        alias = get_current_tenant_db()
        if alias and not name.startswith(self._tenant_prefix(alias)):
            # Legacy keys may still be shared. Keeping them is safer than
            # deleting an object another tenant may reference.
            return
        return super().delete(name)
