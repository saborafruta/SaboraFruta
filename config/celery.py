"""Configuração do Celery para tasks assíncronas."""
import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')

app = Celery('erp_inoovated')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'verificar-vencimentos-diario': {
        'task': 'apps.estoque.tasks.alertas.verificar_vencimentos',
        'schedule': crontab(hour=7, minute=0),
    },
    'bloquear-lotes-vencidos': {
        'task': 'apps.estoque.tasks.alertas.bloquear_lotes_vencidos',
        'schedule': crontab(hour=0, minute=5),
    },
    'verificar-estoque-minimo': {
        'task': 'apps.estoque.tasks.alertas.verificar_estoque_minimo',
        'schedule': crontab(hour=8, minute=0),
    },
}


@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
