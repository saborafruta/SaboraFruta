import logging

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

from apps.core.models import EmpresaBanco
from apps.core.tenant_registry import register_tenant_database


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Aplica migrations pendentes em todos os bancos ativos de empresas.'
    LOCK_ID = 738_214_921

    def add_arguments(self, parser):
        parser.add_argument('--continue-on-error', action='store_true')

    def handle(self, *args, **options):
        bancos = list(
            EmpresaBanco.objects.using('default')
            .filter(ativo=True, status=EmpresaBanco.Status.ATIVO, empresa__ativo=True)
            .select_related('empresa')
            .order_by('pk')
        )
        failed = []
        for banco in bancos:
            try:
                self._migrate_database(banco)
            except Exception as exc:
                logger.exception('Falha ao migrar banco %s.', banco.db_alias)
                EmpresaBanco.objects.using('default').filter(pk=banco.pk).update(
                    status=EmpresaBanco.Status.ERRO,
                    ultimo_erro=str(exc),
                    updated_at=timezone.now(),
                )
                failed.append(banco.db_alias)
                if not options['continue_on_error']:
                    break
        if failed and not options['continue_on_error']:
            raise CommandError(f'Falha ao migrar: {", ".join(failed)}')

    def _migrate_database(self, banco):
        if not register_tenant_database(banco):
            raise RuntimeError('Configuração de conexão indisponível.')
        connection = connections[banco.db_alias]
        locked = False
        try:
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_lock(%s)', [self.LOCK_ID])
                    cursor.execute('SET statement_timeout = 0')
                locked = True
            call_command('migrate', database=banco.db_alias, interactive=False, verbosity=0)
            EmpresaBanco.objects.using('default').filter(pk=banco.pk).update(
                ultima_migracao_em=timezone.now(), ultimo_erro='', updated_at=timezone.now(),
            )
            self.stdout.write(self.style.SUCCESS(f'{banco.db_alias}: migrations OK'))
        finally:
            if locked:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_unlock(%s)', [self.LOCK_ID])
            connection.close()
