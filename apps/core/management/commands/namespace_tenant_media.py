"""Copy each active tenant's referenced media into its own S3 key prefix."""
import hashlib
import json
from collections import defaultdict

from django.apps import apps
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.db.models import FileField
from django.utils import timezone

from apps.core.models import EmpresaBanco
from apps.core.storage import TenantS3Storage
from apps.core.tenant_context import tenant_db
from apps.core.tenant_registry import register_tenant_database
from apps.produtos.models import Produto


class Command(BaseCommand):
    help = 'Isola arquivos referenciados por cada tenant em um prefixo exclusivo.'

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--apply', action='store_true', help='Copia e atualiza os caminhos.')
        group.add_argument('--rollback', metavar='MANIFESTO', help='Restaura os caminhos do manifesto.')

    def handle(self, *args, **options):
        if options['rollback']:
            self._rollback(options['rollback'])
            return

        bancos = list(EmpresaBanco.objects.using('default').filter(
            status=EmpresaBanco.Status.ATIVO,
        ).order_by('db_alias'))
        if not bancos:
            raise CommandError('Nenhum tenant ativo encontrado.')

        models_fields = [
            (model, field)
            for model in apps.get_models()
            if model._meta.app_label in settings.TENANT_ROUTED_APPS
            for field in model._meta.fields
            if isinstance(field, FileField)
        ]
        refs = []
        errors = []
        for banco in bancos:
            alias = banco.db_alias
            if not register_tenant_database(banco):
                errors.append(f'{alias}: conexão não registrada')
                continue
            try:
                with tenant_db(alias):
                    for model, field in models_fields:
                        try:
                            query = model._base_manager.using(alias).exclude(**{field.name: ''})
                            refs.extend(
                                (
                                    alias, model._meta.label_lower, field.name,
                                    pk, name, name,
                                )
                                for pk, name in query.values_list('pk', field.name)
                                if name
                            )
                        except Exception as exc:
                            errors.append(
                                f'{alias}.{model._meta.label_lower}.{field.name}: '
                                f'{type(exc).__name__}'
                            )
            finally:
                connections[alias].close()

        # Produto.foto_url is a URLField, not a FileField. Include only stable
        # keys or signed URLs already pointing at this application's image path.
        for banco in bancos:
            alias = banco.db_alias
            if not register_tenant_database(banco):
                continue
            try:
                with tenant_db(alias):
                    for pk, value in Produto._base_manager.using(alias).exclude(
                        foto_url='',
                    ).values_list('pk', 'foto_url'):
                        storage_name = Produto(foto_url=value).foto_storage_name
                        if storage_name:
                            refs.append((
                                alias, Produto._meta.label_lower, 'foto_url',
                                pk, value, storage_name,
                            ))
            except Exception as exc:
                errors.append(f'{alias}.produtos.Produto.foto_url: {type(exc).__name__}')
            finally:
                connections[alias].close()
        if errors:
            raise CommandError(
                f'Leitura incompleta; nada foi alterado. Ocorrências: {len(errors)} '
                f'({"; ".join(errors[:8])})'
            )

        unique = {}
        for alias, _label, _field, _pk, _old_value, storage_name in refs:
            unique[(alias, storage_name)] = TenantS3Storage.migrated_name(
                alias, storage_name,
            )
        byte_count = 0
        missing = []
        with tenant_db(None):
            for alias, storage_name in unique:
                if not default_storage.exists(storage_name):
                    missing.append((alias, storage_name))
                else:
                    byte_count += default_storage.size(storage_name)
        if missing:
            raise CommandError(
                f'{len(missing)} objeto(s) referenciado(s) não existem no storage; '
                'nada foi alterado.'
            )

        self.stdout.write(
            f'Tenants: {len(bancos)} | referências: {len(refs)} | '
            f'objetos por tenant: {len(unique)} | bytes: {byte_count}'
        )
        if not options['apply']:
            self.stdout.write('Prévia somente leitura. Use --apply para executar.')
            return

        # Make copies first. Old objects stay in place for recovery and rollback.
        for (alias, storage_name), new_name in unique.items():
            with tenant_db(alias):
                source_digest = self._sha256(storage_name)
                if default_storage.exists(new_name):
                    target_digest = self._sha256(new_name)
                else:
                    with default_storage.open(storage_name, 'rb') as source:
                        saved_name = default_storage.save(new_name, source)
                    if saved_name != new_name:
                        raise CommandError(f'O storage alterou a chave de destino para {alias}.')
                    target_digest = self._sha256(new_name)
                if target_digest != source_digest:
                    raise CommandError(f'Falha ao validar cópia de mídia do tenant {alias}.')

        manifest = {
            'created_at': timezone.now().isoformat(),
            'references': [
                {
                    'alias': alias, 'model': label, 'field': field,
                    'pk': pk, 'old': old_value, 'storage_old': storage_name,
                    'new': unique[(alias, storage_name)],
                }
                for alias, label, field, pk, old_value, storage_name in refs
            ],
        }
        manifest_name = (
            'tenant-media-migrations/manifest-'
            f'{timezone.now():%Y%m%dT%H%M%S%f}.json'
        )
        manifest_name = default_storage.save(
            manifest_name,
            ContentFile(json.dumps(manifest, ensure_ascii=False).encode('utf-8')),
        )

        changed = []
        try:
            for alias in sorted({ref[0] for ref in refs}):
                alias_changed = []
                with tenant_db(alias), transaction.atomic(using=alias):
                    for ref_alias, label, field, pk, old_value, storage_name in refs:
                        if ref_alias != alias:
                            continue
                        model = apps.get_model(label)
                        new_name = unique[(alias, storage_name)]
                        updated = model._base_manager.using(alias).filter(
                            pk=pk, **{field: old_value},
                        ).update(**{field: new_name})
                        if updated != 1:
                            raise CommandError(
                                f'Referência alterada durante a migração: {alias}.{label}.{pk}'
                            )
                        alias_changed.append((alias, label, field, pk, old_value, new_name))
                changed.extend(alias_changed)
        except Exception:
            self._restore_rows(changed)
            raise

        self.stdout.write(self.style.SUCCESS(
            f'Mídias isoladas. Manifesto de reversão: {manifest_name}. '
            'As chaves antigas foram preservadas.'
        ))

    def _rollback(self, manifest_name):
        try:
            with default_storage.open(manifest_name, 'rb') as file_obj:
                manifest = json.load(file_obj)
        except Exception as exc:
            raise CommandError(f'Não foi possível ler o manifesto: {type(exc).__name__}') from exc
        changed = [
            (ref['alias'], ref['model'], ref['field'], ref['pk'], ref['old'], ref['new'])
            for ref in manifest.get('references', [])
        ]
        self._restore_rows(changed)
        self.stdout.write(self.style.SUCCESS(
            f'Referências restauradas para as chaves antigas. '
            f'Os objetos novos foram mantidos: {len(changed)} referência(s).'
        ))

    @staticmethod
    def _sha256(name):
        digest = hashlib.sha256()
        with default_storage.open(name, 'rb') as file_obj:
            while True:
                chunk = file_obj.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _restore_rows(changed):
        grouped = defaultdict(list)
        for row in changed:
            grouped[row[0]].append(row)
        conflicts = []
        for alias, rows in grouped.items():
            if alias not in connections.databases and not register_tenant_database(
                EmpresaBanco.objects.using('default').get(db_alias=alias)
            ):
                conflicts.append(alias)
                continue
            with tenant_db(alias), transaction.atomic(using=alias):
                for _alias, label, field, pk, old_name, new_name in rows:
                    model = apps.get_model(label)
                    manager = model._base_manager.using(alias)
                    current = manager.filter(pk=pk).values_list(field, flat=True).first()
                    if current == old_name:
                        continue
                    if current != new_name:
                        conflicts.append(f'{alias}.{label}.{pk}')
                        continue
                    updated = manager.filter(pk=pk, **{field: new_name}).update(
                        **{field: old_name},
                    )
                    if updated != 1:
                        conflicts.append(f'{alias}.{label}.{pk}')
            connections[alias].close()
        if conflicts:
            raise CommandError(
                'Reversão parcial: referências modificadas desde a migração: '
                + ', '.join(conflicts[:8])
            )
