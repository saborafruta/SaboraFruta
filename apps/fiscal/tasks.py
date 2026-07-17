from celery import shared_task

from apps.fiscal.services.ibpt_service import sincronizar_tabela_ibpt


@shared_task(
    name='apps.fiscal.tasks.sincronizar_ibpt_rn',
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={'max_retries': 3},
)
def sincronizar_ibpt_rn():
    return sincronizar_tabela_ibpt('RN')
