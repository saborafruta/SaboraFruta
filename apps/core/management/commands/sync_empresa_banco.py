from django.core.management.base import BaseCommand, CommandError

from apps.core.models import EmpresaBanco
from apps.core.services.empresa_banco_service import EmpresaBancoService


class Command(BaseCommand):
    help = 'Sincroniza cadastros centrais com um banco já migrado.'

    def add_arguments(self, parser):
        parser.add_argument('db_alias')

    def handle(self, *args, **options):
        try:
            banco = EmpresaBanco.objects.using('default').select_related('empresa').get(
                db_alias=options['db_alias']
            )
        except EmpresaBanco.DoesNotExist as exc:
            raise CommandError('Banco não cadastrado.') from exc
        ok, message = EmpresaBancoService.sincronizar_banco(banco)
        if not ok:
            raise CommandError(message)
        self.stdout.write(self.style.SUCCESS(message))
