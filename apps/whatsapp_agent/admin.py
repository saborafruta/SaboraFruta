from django.contrib import admin

from .models import (
    ConfiguracaoWhatsApp, ConfiguracaoWhatsAppCentral, ConversaWhatsApp,
    EnvioResumoWhatsApp, MensagemWhatsApp,
)

admin.site.register([
    ConfiguracaoWhatsApp, ConfiguracaoWhatsAppCentral, ConversaWhatsApp,
    EnvioResumoWhatsApp, MensagemWhatsApp,
])
