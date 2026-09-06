from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Empresa
from apps.core.services.empresa_banco_service import EmpresaBancoService


class Command(BaseCommand):
    help = 'Prepara ou provisiona o banco dedicado de uma empresa sem apagar dados.'

    def add_arguments(self, parser):
        parser.add_argument('empresa_id', type=int)
        parser.add_argument('--migrate', action='store_true')

    def handle(self, *args, **options):
        try:
            empresa = Empresa.objects.using('default').get(pk=options['empresa_id'])
        except Empresa.DoesNotExist as exc:
            raise CommandError('Empresa não encontrada.') from exc
        banco, created = EmpresaBancoService.ensure_for_empresa(empresa)
        ok, message = EmpresaBancoService.solicitar_provisionamento(banco)
        self.stdout.write(message)
        if not ok:
            return
        if options['migrate']:
            ok, message = EmpresaBancoService.migrar_banco(banco)
            self.stdout.write(message)
            if not ok:
                raise CommandError(message)
        elif created:
            self.stdout.write('Banco criado, mas ainda não migrado nem ativado.')
