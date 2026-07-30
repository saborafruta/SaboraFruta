"""
Cobertura de geocodificação por entidade.

    python manage.py cobertura_geo

Serve para conferir o resultado do `geocodificar`: quantos registros ficaram
com coordenada, quantos falharam e por quê.
"""
from django.core.management.base import BaseCommand

from apps.mapas.services.cobertura import formatar_resumo, resumo_geocodificacao


class Command(BaseCommand):
    help = 'Mostra quantos registros de cada entidade já têm coordenada.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--erros', type=int, default=5,
            help='Quantos tipos de erro mais frequentes listar (padrão 5).',
        )

    def handle(self, *args, **opts):
        resumo = resumo_geocodificacao(limite_erros=max(1, opts['erros']))
        for linha in formatar_resumo(resumo):
            self.stdout.write(linha)

        clientes = next(
            (e for e in resumo['entidades'] if e['rotulo'] == 'Clientes'), None,
        )
        if clientes and clientes['sem_coordenada']:
            self.stdout.write(self.style.WARNING(
                f"\n{clientes['sem_coordenada']} cliente(s) sem coordenada. "
                'Rode: manage.py geocodificar'
            ))
        elif clientes and clientes['total']:
            self.stdout.write(self.style.SUCCESS('\nTodos os clientes geocodificados.'))
