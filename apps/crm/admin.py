from django.contrib import admin

from .models import ConfiguracaoFaixasRecompra, RecompraCliente, RecompraControle


@admin.register(ConfiguracaoFaixasRecompra)
class ConfiguracaoFaixasRecompraAdmin(admin.ModelAdmin):
    list_display = ('filial', 'faixa_5_dias', 'faixa_6_dias', 'faixa_7_dias')


@admin.register(RecompraCliente)
class RecompraClienteAdmin(admin.ModelAdmin):
    list_display = (
        'cliente', 'filial', 'frequencia', 'ultima_compra',
        'proxima_compra_prevista', 'dias_restantes', 'status', 'score',
    )
    list_filter = ('status', 'frequencia', 'filial')
    search_fields = ('cliente__razao_social', 'cliente__nome_fantasia', 'cliente__cpf_cnpj')
    # Tudo aqui é derivado do histórico de vendas pelo RecompraService —
    # editar à mão só criaria divergência até o próximo recálculo.
    readonly_fields = [f.name for f in RecompraCliente._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(RecompraControle)
class RecompraControleAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'ultima_execucao')
    readonly_fields = ('ultima_execucao',)
