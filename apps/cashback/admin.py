from django.contrib import admin

from .models import (
    CampanhaCashback,
    CarteiraCashback,
    ConfiguracaoCashback,
    MovimentoCashback,
    RegraCashbackCategoria,
    RegraCashbackEmpresa,
    RegraCashbackFilial,
    RegraCashbackProduto,
)


@admin.register(ConfiguracaoCashback)
class ConfiguracaoCashbackAdmin(admin.ModelAdmin):
    list_display = ("empresa", "filial", "percentual_global", "dias_validade", "ativo")
    list_filter = ("empresa", "ativo")


@admin.register(RegraCashbackProduto)
class RegraCashbackProdutoAdmin(admin.ModelAdmin):
    list_display = ("produto", "percentual", "gera_cashback", "ativo")
    search_fields = ("produto__descricao", "produto__codigo")


@admin.register(RegraCashbackCategoria)
class RegraCashbackCategoriaAdmin(admin.ModelAdmin):
    list_display = ("categoria", "percentual", "gera_cashback", "ativo")


@admin.register(RegraCashbackFilial)
class RegraCashbackFilialAdmin(admin.ModelAdmin):
    list_display = ("filial", "percentual", "ativo")


@admin.register(RegraCashbackEmpresa)
class RegraCashbackEmpresaAdmin(admin.ModelAdmin):
    list_display = ("empresa", "percentual", "ativo")


@admin.register(CampanhaCashback)
class CampanhaCashbackAdmin(admin.ModelAdmin):
    list_display = ("nome", "empresa", "percentual", "data_inicio", "data_fim", "prioridade", "ativo")
    list_filter = ("empresa", "ativo")
    filter_horizontal = ("produtos", "categorias", "filiais")


@admin.register(CarteiraCashback)
class CarteiraCashbackAdmin(admin.ModelAdmin):
    list_display = ("cliente", "empresa", "saldo_disponivel", "saldo_total_gerado")
    search_fields = ("cliente__razao_social", "cliente__cpf_cnpj")
    readonly_fields = (
        "saldo_disponivel", "saldo_pendente", "saldo_expirado",
        "saldo_utilizado", "saldo_cancelado", "saldo_total_gerado",
    )


@admin.register(MovimentoCashback)
class MovimentoCashbackAdmin(admin.ModelAdmin):
    list_display = ("uuid", "cliente", "tipo", "valor", "venda", "origem", "created_at")
    list_filter = ("tipo", "origem")
    search_fields = ("cliente__razao_social", "cliente__cpf_cnpj", "uuid")
    readonly_fields = [f.name for f in MovimentoCashback._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
