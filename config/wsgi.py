import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')
application = get_wsgi_application()

from apps.fiscal.services.ibpt_scheduler import iniciar_agendador_ibpt

iniciar_agendador_ibpt()
