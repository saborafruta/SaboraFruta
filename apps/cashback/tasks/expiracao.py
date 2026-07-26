"""
Task Celery de expiração em lote de créditos de cashback vencidos.

Não é uma dependência para a correção do sistema — a expiração já é
aplicada de forma "preguiçosa" (sob demanda) sempre que uma carteira é
consultada ou usada como pagamento (ver `CashbackWalletService.debitar`/
`saldo_disponivel`), então o saldo mostrado ao cliente nunca considera
crédito vencido, mesmo sem essa task rodando.

Esta task existe para quem quiser rodar um worker Celery e manter os
saldos materializados em lote (ex.: para relatórios). Não está
registrada em `config/celery.py` nesta entrega porque o ambiente de
produção atual não sobe um worker/beat (só gunicorn) — para ativar,
adicione ao `beat_schedule`:

    'expirar-cashback-diario': {
        'task': 'apps.cashback.tasks.expiracao.expirar_creditos_vencidos',
        'schedule': crontab(hour=1, minute=0),
    },
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="apps.cashback.tasks.expiracao.expirar_creditos_vencidos")
def expirar_creditos_vencidos():
    """Expira em lote o saldo disponível de todos os créditos de cashback vencidos."""
    from apps.cashback.services.wallet_service import CashbackWalletService

    total = CashbackWalletService.expirar_creditos()
    logger.info("Expiração de cashback em lote: %s carteira(s) processada(s).", total)
    return total
