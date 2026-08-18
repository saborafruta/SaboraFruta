"""
Cria as grades padrão do vertical Moda numa filial.

    python manage.py seed_grades_moda --filial 1

Reexecutar é seguro: nada é duplicado e uma grade já existente não é
alterada — se alguém ajustou a grade Adulto da casa, o seed não desfaz.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Filial
from apps.moda.models import Grade, ItemGrade, Tamanho

# Ordem espaçada de 10 em 10 para caber um tamanho no meio depois sem
# renumerar os vizinhos (ex.: um "GGG" entre GG e XGG vira ordem 45).
GRADES_PADRAO = {
    Tamanho.Tipo.ADULTO: ('Adulto', ['PP', 'P', 'M', 'G', 'GG', 'XGG']),
    Tamanho.Tipo.PLUS_SIZE: ('Plus Size', ['G1', 'G2', 'G3', 'G4', 'G5']),
    Tamanho.Tipo.INFANTIL: ('Infantil', ['2', '4', '6', '8', '10', '12', '14']),
}


class Command(BaseCommand):
    help = 'Cria as grades padrão (Adulto, Plus Size, Infantil) numa filial.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--filial', type=int, required=True,
            help='ID da filial que vai receber as grades.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            filial = Filial.objects.get(pk=options['filial'])
        except Filial.DoesNotExist:
            raise CommandError(f'Filial {options["filial"]} não existe.')

        self.stdout.write(f'Filial: {filial}')

        for tipo, (nome_grade, siglas) in GRADES_PADRAO.items():
            grade, criou_grade = Grade.objects.get_or_create(
                filial=filial, nome=nome_grade,
                defaults={
                    'tipo': tipo,
                    'padrao': True,
                    'descricao': ' | '.join(siglas),
                },
            )
            if not criou_grade:
                self.stdout.write(f'  {nome_grade}: já existia — mantida como está.')
                continue

            for posicao, sigla in enumerate(siglas, start=1):
                ordem = posicao * 10
                # O tamanho é único por filial, então uma sigla usada em
                # mais de uma grade é reaproveitada em vez de duplicada.
                tamanho, _ = Tamanho.objects.get_or_create(
                    filial=filial, sigla=sigla,
                    defaults={'tipo': tipo, 'ordem': ordem},
                )
                ItemGrade.objects.get_or_create(
                    grade=grade, tamanho=tamanho, defaults={'ordem': ordem},
                )

            self.stdout.write(self.style.SUCCESS(
                f'  {nome_grade}: criada com {len(siglas)} tamanhos '
                f'({" | ".join(siglas)}).'
            ))

        self.stdout.write(self.style.SUCCESS(
            '\n✓ Pronto. Grades personalizadas podem ser criadas pela tela de Grades.'
        ))
