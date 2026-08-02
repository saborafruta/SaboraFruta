"""Expurga pontos antigos do histórico de percurso (§13)."""
from django.core.management.base import BaseCommand

from apps.mapas.services.rastreio import RETENCAO_PADRAO_DIAS, RastreioService


class Command(BaseCommand):
    help = (
        'Apaga pontos do percurso mais antigos que N dias. '
        'O trajeto interessa por alguns dias; guardar meses multiplicaria a '
        'tabela sem ninguém consultar.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias', type=int, default=RETENCAO_PADRAO_DIAS,
            help=f'Retenção em dias (padrão: {RETENCAO_PADRAO_DIAS}).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Mostra quantos seriam apagados, sem apagar.',
        )

    def handle(self, *args, **opts):
        import datetime

        from django.utils import timezone

        from apps.mapas.models import PontoPercurso

        dias = opts['dias']
        corte = timezone.localdate() - datetime.timedelta(days=dias)

        if opts['dry_run']:
            n = PontoPercurso.objects.filter(momento__date__lt=corte).count()
            self.stdout.write(f'{n} ponto(s) anteriores a {corte} seriam apagados.')
            return

        n = RastreioService.expurgar(dias)
        self.stdout.write(self.style.SUCCESS(
            f'{n} ponto(s) anteriores a {corte} apagados.'))
