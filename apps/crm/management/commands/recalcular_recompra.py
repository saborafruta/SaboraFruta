"""
Recalcula o padrão de recompra de todos os clientes.

Serve para o primeiro carregamento (a tabela começa vazia) e como ponto
de entrada para um cron externo, já que não há worker Celery rodando em
produção:

    python manage.py recalcular_recompra
    python manage.py recalcular_recompra --filial 3
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import Filial
from apps.crm.models import RecompraControle
from apps.crm.services import RecompraService


class Command(BaseCommand):
    help = 'Recalcula o padrão de recompra dos clientes (CRM → Alertas de Recompra).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--filial', type=int, default=None,
            help='ID da filial. Sem isso, processa todas as filiais ativas.',
        )

    def handle(self, *args, **options):
        filial_id = options.get('filial')

        filiais = Filial.objects.filter(ativo=True)
        if filial_id:
            filiais = filiais.filter(pk=filial_id)
            if not filiais.exists():
                self.stderr.write(self.style.ERROR(f'Filial #{filial_id} não encontrada ou inativa.'))
                return

        # Uma filial matriz já varre e grava o padrão de todas as filiais
        # da empresa, então as demais filiais dela seriam trabalho repetido.
        # Processa as matrizes primeiro e pula quem já ficou coberto.
        filiais = sorted(
            filiais.select_related('empresa'), key=lambda f: not f.is_matriz,
        )
        empresas_cobertas = set()
        total = 0

        for filial in filiais:
            if filial.empresa_id in empresas_cobertas:
                continue
            if filial.is_matriz:
                empresas_cobertas.add(filial.empresa_id)

            qtd = RecompraService.recalcular(filial)
            total += qtd
            self.stdout.write(f'  {filial}: {qtd} registro(s)')

            RecompraControle.objects.update_or_create(
                empresa_id=filial.empresa_id,
                defaults={'ultima_execucao': timezone.now()},
            )

        self.stdout.write(self.style.SUCCESS(f'Recompra recalculada: {total} registro(s).'))
