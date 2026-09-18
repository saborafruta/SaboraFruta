"""Watchdog operacional e retenção da telemetria do PDV offline."""
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _monitorar_instalacoes_banco_atual():
    from apps.core.models import Filial
    from apps.core.tenant_context import get_current_database_alias
    from apps.pdv.models import InstalacaoPDVOffline
    from apps.pdv.services.offline_monitoring import atualizar_alertas_pdv

    alias = get_current_database_alias() or "default"
    instalacoes = InstalacaoPDVOffline.objects.using("default").filter(
        tenant_alias=alias,
        status=InstalacaoPDVOffline.Status.ATIVA,
    )
    total = 0
    for instalacao in instalacoes.iterator():
        filial = Filial.objects.using(alias).filter(pk=instalacao.filial_id_origem).first()
        if not filial:
            continue
        atualizar_alertas_pdv(alias=alias, filial=filial, instalacao=instalacao)
        total += 1
    return total


@shared_task(name="apps.pdv.tasks.monitorar_pdv_offline")
def monitorar_pdv_offline():
    from apps.core.services.tenant_task_service import TenantTaskService

    return TenantTaskService.executar_em_todos(_monitorar_instalacoes_banco_atual)


@shared_task(name="apps.pdv.tasks.aplicar_retencao_auditoria_offline")
def aplicar_retencao_auditoria_offline():
    from apps.pdv.models import EventoInstalacaoPDVOffline, OcorrenciaPDVOffline, TesteContingenciaPDV

    dias_eventos = int(getattr(settings, "PDV_OFFLINE_AUDIT_RETENTION_DAYS", 180))
    dias_testes = int(getattr(settings, "PDV_OFFLINE_TEST_RETENTION_DAYS", 730))
    limite_eventos = timezone.now() - timezone.timedelta(days=dias_eventos)
    limite_testes = timezone.now() - timezone.timedelta(days=dias_testes)
    eventos, _ = EventoInstalacaoPDVOffline.objects.using("default").filter(
        criado_em__lt=limite_eventos,
    ).delete()
    ocorrencias, _ = OcorrenciaPDVOffline.objects.using("default").filter(
        status=OcorrenciaPDVOffline.Status.RESOLVIDA,
        resolvida_em__lt=limite_eventos,
    ).delete()
    testes, _ = TesteContingenciaPDV.objects.using("default").filter(
        executado_em__lt=limite_testes,
    ).delete()
    logger.info("Retenção PDV offline: eventos=%s ocorrências=%s testes=%s", eventos, ocorrencias, testes)
    return eventos + ocorrencias + testes
