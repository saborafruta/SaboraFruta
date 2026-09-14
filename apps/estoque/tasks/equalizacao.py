"""
Fase 29: rotina diária de equalização, preparada para Celery Beat.

Deliberadamente NÃO registrada em `config/celery.py:beat_schedule` --
o usuário pediu as tarefas prontas, mas desligadas, até decidir ativar.
Pra ligar de verdade, adicionar em `config/celery.py`:

    'equalizacao-diaria': {
        'task': 'apps.estoque.tasks.equalizacao.pipeline_diario_equalizacao',
        'schedule': crontab(hour=6, minute=0),
    },

O pipeline roda os "passos" do pedido original (06:00 recalcular
demanda -> 06:10 estoque ideal -> 06:20 excessos -> 06:30 rupturas ->
06:40 transferências -> 06:50 recomendações -> 07:00 dashboard) como um
só cálculo, porque `calcular_equilibrio` já faz tudo isso numa mesma
passada -- dividir em 7 tarefas separadas recalculando do zero cada
uma seria so' desperdício de CPU sem ganho real. Os nomes dos passos
ficam nos logs, pra quem for debugar a rotina depois.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


def _pipeline_equalizacao_banco_atual() -> dict:
    from apps.core.models import Empresa
    from apps.estoque.services.alertas_equalizacao import sincronizar as sincronizar_alertas
    from apps.estoque.services.snapshot_equalizacao import gerar_snapshot_equilibrio

    total_sugestoes = 0
    total_alertas = 0
    total_empresas = 0
    for empresa in Empresa.objects.all():
        logger.info("equalizacao[%s]: 06:00 recalcular demanda", empresa.pk)
        logger.info("equalizacao[%s]: 06:10 recalcular estoque ideal", empresa.pk)
        alertas = sincronizar_alertas(empresa=empresa)
        logger.info(
            "equalizacao[%s]: 06:20 identificar excessos / 06:30 identificar rupturas -- alertas: %s",
            empresa.pk, alertas,
        )
        logger.info("equalizacao[%s]: 06:40 calcular transferências", empresa.pk)
        logger.info("equalizacao[%s]: 06:50 gerar recomendações", empresa.pk)
        geradas = gerar_snapshot_equilibrio(empresa)
        logger.info("equalizacao[%s]: 07:00 dashboard atualizado (%d sugestões)", empresa.pk, geradas)
        total_sugestoes += geradas
        total_alertas += alertas["detectados"]
        total_empresas += 1

    return {"empresas": total_empresas, "sugestoes": total_sugestoes, "alertas": total_alertas}


@shared_task(name="apps.estoque.tasks.equalizacao.pipeline_diario_equalizacao")
def pipeline_diario_equalizacao():
    from apps.core.services.tenant_task_service import TenantTaskService

    return TenantTaskService.executar_em_todos(_pipeline_equalizacao_banco_atual)
