from django.core.management.base import BaseCommand, CommandError

from apps.core.services.tenant_directory_import_service import (
    TenantDirectoryImportService,
)


class Command(BaseCommand):
    help = 'Importa empresa, filiais, perfis e usuários de um banco existente.'

    def add_arguments(self, parser):
        parser.add_argument('source_alias')
        parser.add_argument('--cnpj', default='')
        parser.add_argument('--database-url-env-var', default='')
        parser.add_argument('--railway-service-id', default='')
        parser.add_argument('--railway-service-name', default='')
        parser.add_argument('--activate', action='store_true')

    def handle(self, *args, **options):
        try:
            banco, resumo = TenantDirectoryImportService.importar(
                options['source_alias'],
                cnpj=options['cnpj'],
                database_url_env_var=options['database_url_env_var'],
                railway_service_id=options['railway_service_id'],
                railway_service_name=options['railway_service_name'],
                ativar=options['activate'],
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f'{banco.empresa}: {resumo["filiais"]} filiais, '
            f'{resumo["perfis"]} perfis e {resumo["usuarios"]} usuários; '
            f'status={banco.status}.',
        ))
