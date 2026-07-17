from __future__ import annotations

import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, connection
from django.utils import timezone

from apps.fiscal.models import AliquotaIBPT
from apps.fiscal.services.ibpt_service import sincronizar_tabela_ibpt

logger = logging.getLogger(__name__)

_LOCK_ID = 5283376844007673894
_iniciado = False
_guard = threading.Lock()


def proxima_execucao(agora):
    alvo = agora.replace(hour=3, minute=10, second=0, microsecond=0)
    if agora >= alvo:
        alvo += timedelta(days=1)
    return alvo


def _adquirir_lock() -> bool:
    if connection.vendor != 'postgresql':
        return True
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [_LOCK_ID])
        return bool(cursor.fetchone()[0])


def _liberar_lock() -> None:
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_unlock(%s)', [_LOCK_ID])


def _sincronizar() -> None:
    close_old_connections()
    bloqueado = False
    try:
        bloqueado = _adquirir_lock()
        if not bloqueado:
            return
        resultado = sincronizar_tabela_ibpt('RN')
        logger.info(
            'Tabela IBPT %s/%s sincronizada: %s registros.',
            resultado['versao'], resultado['uf'], resultado['quantidade'],
        )
    except Exception:
        logger.exception('Falha na sincronizacao automatica da tabela IBPT.')
    finally:
        if bloqueado:
            try:
                _liberar_lock()
            except Exception:
                logger.exception('Falha ao liberar trava da sincronizacao IBPT.')
        close_old_connections()


def _executar_agendador() -> None:
    # Na primeira instalacao, popula a tabela sem bloquear a subida do sistema.
    try:
        hoje = timezone.localdate()
        existe_vigente = AliquotaIBPT.objects.filter(
            uf='RN', vigencia_inicio__lte=hoje, vigencia_fim__gte=hoje
        ).exists()
    except Exception:
        existe_vigente = True
    if not existe_vigente:
        _sincronizar()

    while True:
        agora = timezone.localtime()
        espera = max((proxima_execucao(agora) - agora).total_seconds(), 1)
        threading.Event().wait(espera)
        _sincronizar()


def iniciar_agendador_ibpt() -> None:
    global _iniciado
    if not getattr(settings, 'IBPT_INTERNAL_SCHEDULER', False):
        return
    with _guard:
        if _iniciado:
            return
        _iniciado = True
        threading.Thread(
            target=_executar_agendador,
            name='ibpt-scheduler',
            daemon=True,
        ).start()
