from django.contrib import admin

from .models import ConfiguracaoWhatsApp, ConversaWhatsApp, MensagemWhatsApp

admin.site.register([ConfiguracaoWhatsApp, ConversaWhatsApp, MensagemWhatsApp])
