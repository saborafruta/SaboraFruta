"""
Cria o catálogo de operações da confecção numa filial.

    python manage.py seed_operacoes_moda --filial 1

Reexecutar é seguro: operação já existente não é alterada. Quem ajustou o
tempo padrão da Costura para a realidade da casa não perde o ajuste ao
rodar o comando de novo — e é justamente esse ajuste que o seed não tem
como adivinhar.

Tempo, custo e capacidade saem ZERADOS de propósito. Um número inventado
aqui viraria custo de produto e prazo de entrega sem ninguém perceber que
era chute; zero aparece na tela como pendência e pede para ser preenchido.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Filial
from apps.moda.models import Operacao

S = Operacao.Setor

# Sequência espaçada de 10 em 10 para caber uma operação no meio depois sem
# renumerar as vizinhas (uma "Estamparia digital" entre Corte e Sublimação
# vira sequência 45).
OPERACOES = [
    ('Modelagem', S.MODELAGEM),
    ('Encaixe', S.MODELAGEM),
    ('Corte', S.CORTE),
    ('Separação', S.CORTE),
    ('Sublimação', S.ESTAMPARIA),
    ('Bordado', S.ESTAMPARIA),
    ('Silk', S.ESTAMPARIA),
    ('Preparação', S.COSTURA),
    ('Costura', S.COSTURA),
    ('Revisão', S.QUALIDADE),
    ('Passadoria', S.ACABAMENTO),
    ('Acabamento', S.ACABAMENTO),
    ('Etiquetagem', S.ACABAMENTO),
    ('Embalagem', S.EXPEDICAO),
    ('Expedição', S.EXPEDICAO),
]


class Command(BaseCommand):
    help = 'Cria as 15 operações padrão da confecção numa filial.'

    def add_arguments(self, parser):
        parser.add_argument('--filial', type=int, required=True, help='ID da filial.')

    @transaction.atomic
    def handle(self, *args, **opcoes):
        try:
            filial = Filial.objects.get(pk=opcoes['filial'])
        except Filial.DoesNotExist:
            raise CommandError(f'Filial {opcoes["filial"]} não existe.')

        criadas = 0
        for posicao, (nome, setor) in enumerate(OPERACOES, start=1):
            _operacao, nova = Operacao.all_objects.get_or_create(
                filial=filial, nome=nome,
                defaults={'setor': setor, 'sequencia': posicao * 10},
            )
            if nova:
                criadas += 1
                self.stdout.write(f'  + {nome} ({setor.label})')
            else:
                self.stdout.write(f'  · {nome} já existia — mantida como está')

        self.stdout.write(self.style.SUCCESS(
            f'{criadas} operação(ões) criada(s) em {filial}. '
            'Preencha tempo padrão, custo e capacidade de cada uma na tela '
            'de Operações — sem eles o roteiro não estima custo nem prazo.'
        ))
