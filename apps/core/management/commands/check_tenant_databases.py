from django.core.management.base import BaseCommand

from apps.core.models import EmpresaBanco
from apps.core.services.empresa_banco_service import EmpresaBancoService


class Command(BaseCommand):
    help = 'Testa, sem alterar dados operacionais, a conexão dos bancos cadastrados.'

    def handle(self, *args, **options):
        for banco in EmpresaBanco.objects.using('default').select_related('empresa').order_by('pk'):
            ok, message = EmpresaBancoService.testar_conexao(banco)
            style = self.style.SUCCESS if ok else self.style.ERROR
            self.stdout.write(style(f'{banco.db_alias}: {message}'))
