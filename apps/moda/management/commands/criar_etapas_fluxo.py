"""
Cria as etapas do fluxo em ordens que foram emitidas antes deste recurso.

    python manage.py criar_etapas_fluxo --filial 1
    python manage.py criar_etapas_fluxo --todas

Existe porque as ordens já emitidas não têm etapa nenhuma: o fluxo passou a
nascer junto com a OP só a partir daqui. Sem este comando, quem já usava
ordens veria o fluxo vazio e não teria como preenchê-lo.

Reexecutar é seguro: etapa existente não é recriada nem alterada, então
apontamento já feito não se perde.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Filial
from apps.moda.models import OrdemProducao
from apps.moda.services import FluxoService


class Command(BaseCommand):
    help = 'Cria as etapas do fluxo de produção em ordens antigas.'

    def add_arguments(self, parser):
        parser.add_argument('--filial', type=int, help='ID da filial.')
        parser.add_argument(
            '--todas', action='store_true',
            help='Todas as filiais. Exclusivo com --filial.',
        )

    @transaction.atomic
    def handle(self, *args, **opcoes):
        filial_id, todas = opcoes.get('filial'), opcoes.get('todas')
        if bool(filial_id) == bool(todas):
            raise CommandError('Informe --filial <id> OU --todas.')

        ordens = OrdemProducao.all_objects.prefetch_related('etapas')
        if filial_id:
            if not Filial.objects.filter(pk=filial_id).exists():
                raise CommandError(f'Filial {filial_id} não existe.')
            ordens = ordens.filter(filial_id=filial_id)

        tocadas = criadas = 0
        for ordem in ordens:
            novas = FluxoService.criar_etapas(ordem)
            if novas:
                tocadas += 1
                criadas += len(novas)
                self.stdout.write(f'  + {ordem.numero}: {len(novas)} etapa(s)')

        self.stdout.write(self.style.SUCCESS(
            f'{criadas} etapa(s) criada(s) em {tocadas} ordem(ns). '
            'Ordens que já tinham o fluxo montado não foram alteradas.'
        ))
