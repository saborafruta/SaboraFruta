"""Tarefas de reconciliacao de documentos fiscais enviados a Focus."""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


def _reconciliar_nfce_banco_atual():
    from apps.financeiro.constants.enums import StatusDocumentoFiscal
    from apps.financeiro.models.fiscal import DocumentoFiscal
    from apps.fiscal.integrations.focusnfe import FocusNFeClient
    from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig
    from apps.fiscal.integrations.focusnfe.exceptions import (
        FocusNFeError,
        FocusNFeNotFoundError,
    )
    from apps.fiscal.services.focusnfe_service import FocusNFeService

    agora = timezone.now()
    documentos = list(
        DocumentoFiscal.objects.filter(
            tipo_documento='nfce',
            status=StatusDocumentoFiscal.PROCESSANDO,
            data_emissao__gte=agora - timedelta(days=2),
            updated_at__lte=agora - timedelta(minutes=1),
        )
        .select_related('filial')
        .order_by('data_emissao')[:100]
    )
    resumo = {'consultados': 0, 'atualizados': 0, 'erros': 0}
    for documento in documentos:
        token = (documento.filial.focusnfe_token or '').strip()
        if not token:
            resumo['erros'] += 1
            continue
        service = FocusNFeService(client=FocusNFeClient(config=FocusNFeConfig.from_env(
            token=token,
            ambiente=documento.filial.focusnfe_ambiente,
        )))
        resumo['consultados'] += 1
        try:
            service.consultar(documento)
            resumo['atualizados'] += 1
        except FocusNFeNotFoundError:
            if not documento.resultado_envio_incerto or not documento.payload_envio:
                resumo['erros'] += 1
                continue
            try:
                # A consulta 404 elimina a ambiguidade: a Focus nao recebeu a
                # referencia. Reenvie o snapshot original, sem trocar numero.
                service.emitir(documento, documento.payload_envio)
                resumo['atualizados'] += 1
            except FocusNFeError:
                resumo['erros'] += 1
                logger.warning('Falha ao reenviar NFC-e %s', documento.pk, exc_info=True)
        except FocusNFeError:
            resumo['erros'] += 1
            logger.warning('Falha ao reconciliar NFC-e %s', documento.pk, exc_info=True)
    logger.info('Reconciliacao automatica de NFC-e concluida: %s', resumo)
    return resumo['atualizados']


@shared_task(name='apps.fiscal.tasks.reconciliar_nfce_processando')
def reconciliar_nfce_processando():
    from apps.core.services.tenant_task_service import TenantTaskService

    return TenantTaskService.executar_em_todos(_reconciliar_nfce_banco_atual)
