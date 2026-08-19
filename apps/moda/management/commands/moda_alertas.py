"""
Varre os alertas do vertical e reconcilia o sino de notificações.

Feito para rodar de hora em hora, por filial. Não é o que faz os alertas
existirem — a tela de alertas e o dashboard detectam ao vivo, sem depender
deste comando. O que ele faz é EMPURRAR: colocar no sino o que ninguém foi
olhar, e tirar de lá o que já se resolveu.

Roda sem argumento para todas as filiais com o módulo Moda ativo; com
`--filial <id>` para uma só, que é o modo de conferir o resultado à mão
antes de agendar.
"""
from django.core.management.base import BaseCommand

from apps.core.models import Filial
from apps.core.services.modulos import modulos_ativos
from apps.moda.services.alertas import AlertaService


class Command(BaseCommand):
    help = 'Detecta os alertas do vertical Moda e sincroniza as notificações.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--filial', type=int, default=None,
            help='Roda só nesta filial (id). Sem isto, roda em todas as que têm o módulo.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Só lista o que seria criado, sem tocar nas notificações.',
        )

    def handle(self, *args, **opcoes):
        filiais = self._filiais(opcoes['filial'])
        if not filiais:
            self.stdout.write(self.style.WARNING(
                'Nenhuma filial com o módulo Moda ativo.'
            ))
            return

        for filial in filiais:
            if opcoes['dry_run']:
                alertas = AlertaService.detectar(filial)
                self.stdout.write(f'{filial}: {len(alertas)} alertas')
                for alerta in alertas:
                    marca = '🔴' if alerta.critico else '🟡'
                    self.stdout.write(f'  {marca} {alerta.titulo}')
                continue

            resultado = AlertaService.sincronizar(filial)
            self.stdout.write(self.style.SUCCESS(
                f'{filial}: {resultado["detectados"]} alertas '
                f'({resultado["criados"]} novos, '
                f'{resultado["desligados"]} resolvidos)'
            ))

    @staticmethod
    def _filiais(filial_id):
        if filial_id:
            return list(Filial.objects.filter(pk=filial_id))
        # `modulos_ativos` recebe a FILIAL, não a empresa: o segmento da
        # empresa concede, e a filial pode ter desligado o módulo. Passar a
        # empresa alertaria filial que desativou o vertical.
        return [
            f for f in Filial.objects.filter(ativo=True).select_related('empresa')
            if 'moda' in modulos_ativos(f)
        ]
