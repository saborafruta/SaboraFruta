from django.core.management.base import BaseCommand

from apps.core.models import EmpresaBanco
from apps.core.services.tenant_public_link_service import TenantPublicLinkService


class Command(BaseCommand):
    help = 'Reconstrói o índice central dos links públicos existentes.'

    def handle(self, *args, **options):
        total = 0
        for banco in EmpresaBanco.objects.using('default').filter(
            ativo=True, status=EmpresaBanco.Status.ATIVO,
        ).order_by('pk'):
            count = TenantPublicLinkService.rebuild_for_banco(banco)
            total += count
            self.stdout.write(f'{banco.db_alias}: {count} links')
        self.stdout.write(self.style.SUCCESS(f'Índice concluído: {total} links.'))
