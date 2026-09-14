from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.core.context_processors import orla_widget_host_enabled


@never_cache
@require_GET
def health_check(request):
    checks = {
        'database': False,
        'media_root': False,
    }
    status_code = 200

    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        checks['database'] = True
    except Exception:
        status_code = 503

    try:
        media_root = settings.MEDIA_ROOT
        checks['media_root'] = media_root.exists() and media_root.is_dir()
    except Exception:
        status_code = 503

    if not all(checks.values()):
        status_code = 503

    if getattr(settings, 'ORLA_WIDGET_SIGNING_PRIVATE_KEY', ''):
        identity_mode = 'ed25519'
    elif getattr(settings, 'ORLA_WIDGET_SIGNING_SECRET', ''):
        identity_mode = 'hmac'
    else:
        identity_mode = 'anonymous'

    return JsonResponse({
        'status': 'ok' if status_code == 200 else 'degraded',
        'checks': checks,
        'integrations': {
            'orla_widget': orla_widget_host_enabled(request),
            'orla_widget_identity': identity_mode,
        },
    }, status=status_code)
