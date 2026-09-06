"""
Task Celery de recálculo em lote do padrão de recompra.

NÃO está registrada em `config/celery.py` — o container de produção sobe
apenas o gunicorn (ver o `CMD` do Dockerfile), sem worker nem beat, então
registrá-la daria a falsa impressão de que roda sozinha.

A correção do sistema não depende dela: o padrão é atualizado na hora em
que a venda é faturada/finalizada, e a tela revalida em lote o que estiver
obsoleto (`RecompraService.recalcular_se_obsoleto`). Esta task existe para
quando houver um worker, ou como alvo de um cron externo — nesse caso
prefira o management command `recalcular_recompra`.

Para ativar, com um worker no ar, acrescente ao `beat_schedule`:

    'recalcular-recompra-diario': {
        'task': 'apps.crm.tasks.recompra.recalcular_recompra_todas_filiais',
        'schedule': crontab(hour=6, minute=0),
    },
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='apps.crm.tasks.recompra.recalcular_recompra_todas_filiais')
def recalcular_recompra_todas_filiais():
    """Recalcula o padrão de recompra de todas as filiais ativas."""
    from django.core.management import call_command
    from apps.core.services.tenant_task_service import TenantTaskService

    def executar():
        call_command('recalcular_recompra')
        return 0

    TenantTaskService.executar_em_todos(executar)
    logger.info('Recálculo de recompra concluído.')
