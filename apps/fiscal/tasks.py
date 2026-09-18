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


def _monitorar_pendencias_fiscais_banco_atual():
    from apps.core.models import Notificacao, NotificacaoLeitura
    from apps.financeiro.constants.enums import StatusDocumentoFiscal
    from apps.financeiro.models.fiscal import DocumentoFiscal
    from django.urls import reverse

    limite = timezone.now() - timedelta(minutes=5)
    documentos = DocumentoFiscal.objects.filter(
        tipo_documento="nfce",
        updated_at__lte=limite,
    ).filter(
        status=StatusDocumentoFiscal.PROCESSANDO,
    ).select_related("filial")[:500]
    ativos = set()
    for documento in documentos:
        referencia_id = str(documento.pk)
        ativos.add(referencia_id)
        titulo = (
            "NFC-e em contingência ainda pendente"
            if documento.em_contingencia else "NFC-e processando há mais de 5 minutos"
        )
        existente = Notificacao.objects.filter(
            filial=documento.filial,
            tipo=Notificacao.Tipo.ALERTA_SISTEMA,
            referencia_tipo="nfce_pendente",
            referencia_id=referencia_id,
        ).first()
        estava_ativa = bool(existente and existente.ativa)
        notificacao, _ = Notificacao.objects.update_or_create(
            filial=documento.filial,
            tipo=Notificacao.Tipo.ALERTA_SISTEMA,
            referencia_tipo="nfce_pendente",
            referencia_id=referencia_id,
            defaults={
                "titulo": titulo,
                "mensagem": f"NFC-e {documento.numero}/{documento.serie}: consulte e reconcilie o retorno fiscal.",
                "url": reverse("fiscal:documento-saida-detail", args=[documento.pk]),
                "ativa": True,
            },
        )
        if not estava_ativa:
            NotificacaoLeitura.objects.filter(notificacao=notificacao).delete()
    obsoletas = Notificacao.objects.filter(
        tipo=Notificacao.Tipo.ALERTA_SISTEMA,
        referencia_tipo="nfce_pendente",
        ativa=True,
    )
    if ativos:
        obsoletas = obsoletas.exclude(referencia_id__in=ativos)
    return obsoletas.update(ativa=False) + len(ativos)


@shared_task(name="apps.fiscal.tasks.monitorar_pendencias_fiscais")
def monitorar_pendencias_fiscais():
    from apps.core.services.tenant_task_service import TenantTaskService

    return TenantTaskService.executar_em_todos(_monitorar_pendencias_fiscais_banco_atual)
