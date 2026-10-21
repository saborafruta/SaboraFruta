from django.contrib import admin

from .models import (
    AtividadeCRM, ConfiguracaoFaixasRecompra, EtapaPipelineCRM, OportunidadeCRM,
    PipelineCRM, RecompraCliente, RecompraControle, TagCRM,
)


class EtapaPipelineInline(admin.TabularInline):
    model = EtapaPipelineCRM
    extra = 0


@admin.register(PipelineCRM)
class PipelineCRMAdmin(admin.ModelAdmin):
    list_display = ('nome', 'filial', 'padrao', 'ativo')
    list_filter = ('padrao', 'ativo', 'filial')
    inlines = [EtapaPipelineInline]


@admin.register(TagCRM)
class TagCRMAdmin(admin.ModelAdmin):
    list_display = ('nome', 'filial', 'cor', 'ativo')
    list_filter = ('ativo', 'filial')


@admin.register(OportunidadeCRM)
class OportunidadeCRMAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'cliente', 'filial', 'etapa', 'responsavel', 'valor_estimado')
    list_filter = ('filial', 'pipeline', 'etapa', 'prioridade', 'origem')
    search_fields = ('titulo', 'cliente__razao_social', 'cliente__nome_fantasia')
    filter_horizontal = ('tags',)


@admin.register(AtividadeCRM)
class AtividadeCRMAdmin(admin.ModelAdmin):
    list_display = ('oportunidade', 'tipo', 'autor', 'created_at')
    list_filter = ('tipo',)
    readonly_fields = ('created_at', 'updated_at')


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
