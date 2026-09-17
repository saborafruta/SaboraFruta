"""Reconcilia NFC-e pendente mesmo em instalacoes sem worker Celery."""
import logging
import threading

from django.conf import settings
from django.db import close_old_connections, connection

from apps.fiscal.tasks import reconciliar_nfce_processando

logger = logging.getLogger(__name__)

_LOCK_ID = 5283376844007673895
_INTERVALO_SEGUNDOS = 120
_iniciado = False
_guard = threading.Lock()


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


def _reconciliar() -> None:
    close_old_connections()
    bloqueado = False
    try:
        bloqueado = _adquirir_lock()
        if bloqueado:
            reconciliar_nfce_processando.run()
    except Exception:
        logger.exception('Falha no agendador interno de reconciliacao da NFC-e.')
    finally:
        if bloqueado:
            try:
                _liberar_lock()
            except Exception:
                logger.exception('Falha ao liberar trava da reconciliacao da NFC-e.')
        close_old_connections()


def _executar_agendador() -> None:
    while True:
        threading.Event().wait(_INTERVALO_SEGUNDOS)
        _reconciliar()


def iniciar_agendador_nfce() -> None:
    global _iniciado
    if not getattr(settings, 'NFCE_RECONCILIATION_INTERNAL_SCHEDULER', False):
        return
    with _guard:
        if _iniciado:
            return
        _iniciado = True
        threading.Thread(
            target=_executar_agendador,
            name='nfce-reconciliation-scheduler',
            daemon=True,
        ).start()
