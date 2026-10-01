from celery import shared_task

from .conversation_service import encerrar_conversas_inativas


@shared_task(name='apps.whatsapp_agent.tasks.encerrar_conversas_inativas')
def encerrar_conversas_inativas_task():
    return encerrar_conversas_inativas()
