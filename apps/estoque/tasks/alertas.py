"""Tasks Celery de monitoramento de estoque.

Schedule (definido em config/celery.py):
- verificar_vencimentos: 07:00 diariamente
- bloquear_lotes_vencidos: 00:05 diariamente
- verificar_estoque_minimo: 08:00 diariamente
"""
import logging
from decimal import Decimal

from celery import shared_task
from django.db.models import DecimalField, F, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

logger = logging.getLogger(__name__)


def _verificar_vencimentos_banco_atual():
    """Percorre lotes ativos e gera/atualiza alertas para vencimentos em ate 60 dias."""
    from apps.estoque.models import LoteProduto
    from apps.estoque.services.alerta_service import AlertaService

    hoje = timezone.localdate()
    limite = hoje.fromordinal(hoje.toordinal() + 60)

    lotes = LoteProduto.objects.filter(
        status=LoteProduto.Status.ATIVO,
        quantidade_atual__gt=0,
        data_validade__isnull=False,
        data_validade__gte=hoje,
        data_validade__lte=limite,
    ).select_related('produto', 'filial')

    gerados = 0
    for lote in lotes:
        if AlertaService.gerar_alertas_lote(lote):
            gerados += 1

    logger.info('verificar_vencimentos: %d alertas gerados/atualizados', gerados)
    return gerados


@shared_task(name='apps.estoque.tasks.alertas.verificar_vencimentos')
def verificar_vencimentos():
    from apps.core.services.tenant_task_service import TenantTaskService

    return TenantTaskService.executar_em_todos(_verificar_vencimentos_banco_atual)


def _bloquear_lotes_vencidos_banco_atual():
    """Bloqueia automaticamente todos os lotes vencidos."""
    from apps.estoque.models import LoteProduto
    from apps.estoque.services.alerta_service import AlertaService

    hoje = timezone.localdate()
    lotes = LoteProduto.objects.filter(
        status=LoteProduto.Status.ATIVO,
        data_validade__isnull=False,
        data_validade__lt=hoje,
    )
    bloqueados = 0
    for lote in lotes:
        if AlertaService.bloquear_lote_vencido(lote):
            bloqueados += 1

    logger.info('bloquear_lotes_vencidos: %d lotes bloqueados', bloqueados)
    return bloqueados


@shared_task(name='apps.estoque.tasks.alertas.bloquear_lotes_vencidos')
def bloquear_lotes_vencidos():
    from apps.core.services.tenant_task_service import TenantTaskService

    return TenantTaskService.executar_em_todos(_bloquear_lotes_vencidos_banco_atual)


def _verificar_estoque_minimo_banco_atual():
    """Conta produtos vinculados a filiais com estoque abaixo do minimo."""
    from apps.estoque.models import Estoque
    from apps.produtos.models import ProdutoFilial

    quantidade_field = DecimalField(max_digits=12, decimal_places=3)
    estoque_qs = Estoque.objects.filter(
        produto_id=OuterRef('produto_id'),
        filial_id=OuterRef('filial_id'),
    )
    criticos = ProdutoFilial.objects.filter(
        ativo=True,
        produto__ativo=True,
        produto__estoque_minimo__gt=0,
    ).annotate(
        quantidade_disponivel=Coalesce(
            Subquery(
                estoque_qs.values('quantidade_disponivel')[:1],
                output_field=quantidade_field,
            ),
            Value(Decimal('0'), output_field=quantidade_field),
            output_field=quantidade_field,
        ),
    ).filter(
        quantidade_disponivel__lt=F('produto__estoque_minimo'),
    ).select_related('produto', 'filial')

    count = criticos.count()
    # Hook: enviar email/webhook aqui.
    logger.info('verificar_estoque_minimo: %d produtos com estoque abaixo do minimo', count)
    return count


@shared_task(name='apps.estoque.tasks.alertas.verificar_estoque_minimo')
def verificar_estoque_minimo():
    from apps.core.services.tenant_task_service import TenantTaskService

    return TenantTaskService.executar_em_todos(_verificar_estoque_minimo_banco_atual)
