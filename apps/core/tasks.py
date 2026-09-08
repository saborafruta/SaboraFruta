"""Tasks Celery do modulo administrativo."""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name='apps.core.tasks.executar_separacao_filial',
    acks_late=True,
    reject_on_worker_lost=True,
)
def executar_separacao_filial(processo_id, usuario_id=None):
    """Executa a separação de filial fora da requisição web."""
    from apps.core.services.separacao_filial_service import SeparacaoFilialService

    try:
        SeparacaoFilialService.executar_por_id(processo_id, usuario_id)
    except Exception:
        logger.exception('Falha ao executar separacao de filial %s', processo_id)
        raise
    return processo_id
