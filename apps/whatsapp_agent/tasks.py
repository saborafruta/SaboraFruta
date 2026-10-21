from celery import shared_task

from .conversation_service import encerrar_conversas_inativas
from .cobranca_service import agendar_cobrancas_automaticas, processar_proxima_cobranca
from .crm_service import recuperar_agendamentos_abandonados
from .resumo_service import preparar_resumos_diarios, processar_proximo_resumo


@shared_task(name='apps.whatsapp_agent.tasks.encerrar_conversas_inativas')
def encerrar_conversas_inativas_task():
    return encerrar_conversas_inativas()


@shared_task(name='apps.whatsapp_agent.tasks.recuperar_agendamentos_abandonados')
def recuperar_agendamentos_abandonados_task():
    return recuperar_agendamentos_abandonados()


@shared_task(name='apps.whatsapp_agent.tasks.preparar_resumos_diarios')
def preparar_resumos_diarios_task():
    return preparar_resumos_diarios()


@shared_task(name='apps.whatsapp_agent.tasks.processar_proximo_resumo')
def processar_proximo_resumo_task():
    return processar_proximo_resumo()


@shared_task(name='apps.whatsapp_agent.tasks.agendar_cobrancas_automaticas')
def agendar_cobrancas_automaticas_task():
    return agendar_cobrancas_automaticas()


@shared_task(name='apps.whatsapp_agent.tasks.processar_proxima_cobranca')
def processar_proxima_cobranca_task():
    return processar_proxima_cobranca()
