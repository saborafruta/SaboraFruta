from django.core.management.base import BaseCommand, CommandError

from apps.fiscal.services.ibpt_service import sincronizar_tabela_ibpt


class Command(BaseCommand):
    help = 'Sincroniza a tabela IBPT vigente para uma UF.'

    def add_arguments(self, parser):
        parser.add_argument('--uf', default='RN')

    def handle(self, *args, **options):
        try:
            resultado = sincronizar_tabela_ibpt(options['uf'])
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"IBPT {resultado['versao']}/{resultado['uf']}: "
            f"{resultado['quantidade']} registros sincronizados."
        ))
