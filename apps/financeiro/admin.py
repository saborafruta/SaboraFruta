from django.contrib import admin
from apps.financeiro.models import (
    ClasseFiscal, ClasseFiscalAliquota, NaturezaOperacao,
    DocumentoFiscal, ItemDocumentoFiscal,
    FormaPagamento, CondicaoPagamento,
    ContaBancaria, PlanoContas, CentroCusto,
    ContaReceber, ContaPagar, PagamentoContaPagar,
    PIXCobranca, Boleto,
    ExtratoBancario, ConciliacaoBancaria, AgendaPagamento,
    DREConsolidado,
)


class AliquotaInline(admin.TabularInline):
    model = ClasseFiscalAliquota
    extra = 0


@admin.register(ClasseFiscal)
class ClasseFiscalAdmin(admin.ModelAdmin):
    list_display = ["codigo", "descricao", "empresa", "ativo"]
    inlines = [AliquotaInline]


@admin.register(NaturezaOperacao)
class NatOpAdmin(admin.ModelAdmin):
    list_display = ["descricao", "tipo", "cfop_dentro_estado",
                    "cfop_fora_estado", "ativo"]


class ItemDocFiscalInline(admin.TabularInline):
    model = ItemDocumentoFiscal
    extra = 0


@admin.register(DocumentoFiscal)
class DocFiscalAdmin(admin.ModelAdmin):
    list_display = ["tipo_documento", "numero", "serie", "filial",
                    "valor_total", "status", "data_emissao"]
    list_filter = ["filial", "tipo_documento", "status"]
    search_fields = ["numero", "chave", "protocolo"]
    inlines = [ItemDocFiscalInline]
    date_hierarchy = "data_emissao"


@admin.register(FormaPagamento)
class FormaPagamentoAdmin(admin.ModelAdmin):
    list_display = ["descricao", "tipo", "filial", "codigo_sefaz", "ativo"]
    list_filter = ["empresa", "filial", "tipo", "ativo"]
    search_fields = ["descricao"]


@admin.register(CondicaoPagamento)
class CondicaoPagamentoAdmin(admin.ModelAdmin):
    list_display = ["descricao", "numero_parcelas", "intervalo_dias", "ativo"]


@admin.register(ContaBancaria)
class ContaBancariaAdmin(admin.ModelAdmin):
    list_display = ["banco_nome", "agencia", "conta", "filial", "saldo_atual", "ativo"]


@admin.register(PlanoContas)
class PlanoContasAdmin(admin.ModelAdmin):
    list_display = ["codigo", "descricao", "tipo", "nivel", "ativo"]
    list_filter = ["tipo", "ativo"]


@admin.register(CentroCusto)
class CentroCustoAdmin(admin.ModelAdmin):
    list_display = ["codigo", "nome", "empresa", "ativo"]
    list_filter = ["empresa", "ativo"]
    search_fields = ["codigo", "nome"]


@admin.register(ContaReceber)
class CRAdmin(admin.ModelAdmin):
    list_display = ["documento_numero", "parcela", "cliente", "valor_final",
                    "data_vencimento", "status"]
    list_filter = ["filial", "status"]
    date_hierarchy = "data_vencimento"


@admin.register(ContaPagar)
class CPAdmin(admin.ModelAdmin):
    list_display = ["documento_numero", "parcela", "fornecedor", "valor_final",
                    "data_vencimento", "status"]
    list_filter = ["filial", "status"]
    date_hierarchy = "data_vencimento"


@admin.register(PagamentoContaPagar)
class PagamentoContaPagarAdmin(admin.ModelAdmin):
    list_display = ["conta_pagar", "data_pagamento", "valor_pago", "forma_pagamento", "filial"]
    list_filter = ["filial", "forma_pagamento"]
    date_hierarchy = "data_pagamento"


admin.site.register(PIXCobranca)
admin.site.register(Boleto)
admin.site.register(ExtratoBancario)
admin.site.register(ConciliacaoBancaria)
admin.site.register(AgendaPagamento)


@admin.register(DREConsolidado)
class DREAdmin(admin.ModelAdmin):
    list_display = ["competencia", "filial", "linha_producao", "receita_liquida",
                    "lucro_liquido", "margem_liquida_percentual"]
    list_filter = ["filial", "linha_producao"]
    date_hierarchy = "competencia"
