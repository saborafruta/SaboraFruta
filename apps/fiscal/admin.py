from django.contrib import admin

from apps.fiscal.models import AliquotaIBPT, RegraFiscalUF, TabelaFiscalAuxiliar


@admin.register(TabelaFiscalAuxiliar)
class TabelaFiscalAuxiliarAdmin(admin.ModelAdmin):
    list_display = ['tipo', 'codigo', 'descricao', 'uf', 'ncm', 'cest', 'aliquota', 'fonte', 'ativo']
    list_filter = ['tipo', 'uf', 'ativo']
    search_fields = ['codigo', 'descricao', 'ncm', 'cest', 'fonte']


@admin.register(RegraFiscalUF)
class RegraFiscalUFAdmin(admin.ModelAdmin):
    list_display = ['uf', 'ncm', 'cest', 'cfop', 'regime_tributario', 'aliquota_icms', 'fonte', 'ativo']
    list_filter = ['uf', 'regime_tributario', 'ativo']
    search_fields = ['ncm', 'cest', 'cfop', 'fonte', 'observacao']


@admin.register(AliquotaIBPT)
class AliquotaIBPTAdmin(admin.ModelAdmin):
    list_display = [
        'ncm', 'uf', 'federal_nacional', 'federal_importado', 'estadual',
        'municipal', 'versao', 'vigencia_inicio', 'vigencia_fim',
    ]
    list_filter = ['uf', 'versao', 'vigencia_inicio', 'vigencia_fim']
    search_fields = ['ncm', 'descricao', 'fonte']
