from django.contrib import admin

from .models import CredencialIntegracao


@admin.register(CredencialIntegracao)
class CredencialIntegracaoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'empresa', 'prefixo', 'ativo', 'expira_em', 'ultimo_uso_em')
    list_filter = ('ativo', 'empresa')
    search_fields = ('nome', 'empresa__razao_social', 'empresa__cnpj', 'prefixo')
    filter_horizontal = ('filiais',)
    readonly_fields = ('prefixo', 'token_hash', 'ultimo_uso_em', 'ultimo_ip', 'criado_em', 'atualizado_em')

    def has_add_permission(self, request):
        # A criação passa pelo comando próprio, que mostra o segredo uma única
        # vez. O formulário do Admin não teria como recuperar esse segredo.
        return False
