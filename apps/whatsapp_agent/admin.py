from django.contrib import admin

from .models import (
    ConexaoWhatsAppVendedor, ConfiguracaoWhatsApp, ConfiguracaoWhatsAppCentral, ConversaWhatsApp,
    EnvioResumoWhatsApp, MensagemWhatsApp,
    UsoIAWhatsApp,
)

admin.site.register([
    ConexaoWhatsAppVendedor, ConfiguracaoWhatsApp, ConfiguracaoWhatsAppCentral, ConversaWhatsApp,
    EnvioResumoWhatsApp, MensagemWhatsApp,
    UsoIAWhatsApp,
])
