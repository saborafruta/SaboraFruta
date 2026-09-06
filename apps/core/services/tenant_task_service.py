"""Executa rotinas de fundo uma vez para cada banco de empresa ativo."""
import logging

from django.conf import settings
from django.db import connections

from apps.core.models import EmpresaBanco
from apps.core.tenant_context import tenant_db
from apps.core.tenant_registry import register_tenant_database


logger = logging.getLogger(__name__)


class TenantTaskService:
    @classmethod
    def executar_em_todos(cls, callback):
        """Executa ``callback`` no banco atual ou em todos os tenants ativos.

        O retorno continua sendo um inteiro agregado, preservando o contrato das
        tasks existentes. Uma falha não é mascarada: o Celery deve registrar e
        repetir a task, em vez de deixar uma empresa sem processamento.
        """
        if not settings.TENANT_DATABASE_ROUTING_ENABLED:
            return callback()

        total = 0
        bancos = EmpresaBanco.objects.using('default').filter(
            ativo=True,
            status=EmpresaBanco.Status.ATIVO,
        ).order_by('pk')
        for banco in bancos.iterator():
            if not register_tenant_database(banco):
                raise RuntimeError(f'Conexão indisponível para {banco.db_alias}.')
            try:
                with tenant_db(banco.db_alias):
                    total += callback() or 0
            except Exception:
                logger.exception(
                    'Falha em task multibanco.',
                    extra={'tenant_alias': banco.db_alias},
                )
                raise
            finally:
                connections[banco.db_alias].close()
        return total
