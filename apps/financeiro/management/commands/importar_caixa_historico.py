import json
import sys
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from apps.core.models import Filial
from apps.financeiro.services.caixa_historico_service import importar_historico


class Command(BaseCommand):
    help = 'Importa JSON auditado para o histórico isolado do caixa. Simula por padrão.'

    def add_arguments(self, parser):
        parser.add_argument('arquivo', help='Caminho do JSON ou - para stdin')
        parser.add_argument('--filial', type=int, required=True)
        parser.add_argument('--cnpj', required=True)
        parser.add_argument('--inicio', type=date.fromisoformat, required=True)
        parser.add_argument('--fim', type=date.fromisoformat, required=True)
        parser.add_argument('--aplicar', action='store_true')

    def handle(self, *args, **options):
        try:
            texto = sys.stdin.read() if options['arquivo'] == '-' else Path(options['arquivo']).read_text(encoding='utf-8')
            resultado = importar_historico(json.loads(texto), filial_id=options['filial'],
                                           cnpj=options['cnpj'], inicio=options['inicio'],
                                           fim=options['fim'], aplicar=options['aplicar'])
        except (ValueError, KeyError, TypeError, OSError, Filial.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(resultado, ensure_ascii=False))
