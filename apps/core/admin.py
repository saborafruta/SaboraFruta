from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.core.models import (
    Empresa, Filial, LogAcesso, LogSistema, PerfilAcesso, Permissao, RegistroAuditoria,
    PoliticaReplicacao, PoliticaReplicacaoFilial, SessaoUsuario, Usuario,
    UsuarioFilialAcesso, EmpresaBanco,
)


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ['razao_social', 'nome_fantasia', 'cnpj', 'regime_tributario', 'ativo']
    list_filter = ['ativo', 'regime_tributario']
    search_fields = ['razao_social', 'nome_fantasia', 'cnpj']


@admin.register(EmpresaBanco)
class EmpresaBancoAdmin(admin.ModelAdmin):
    list_display = [
        'empresa', 'db_alias', 'status', 'provisionamento_modo',
        'railway_database_service_name', 'ultima_verificacao_em',
    ]
    list_filter = ['status', 'provisionamento_modo', 'ativo']
    search_fields = [
        'empresa__razao_social', 'empresa__nome_fantasia', 'empresa__cnpj',
        'db_alias', 'railway_database_service_name',
    ]
    autocomplete_fields = ['empresa']
    readonly_fields = [
        'provisionamento_solicitado_em', 'provisionado_em',
        'ultima_migracao_em', 'ultima_verificacao_em', 'ultimo_erro',
    ]


@admin.register(Filial)
class FilialAdmin(admin.ModelAdmin):
    list_display = ['nome_fantasia', 'razao_social', 'empresa', 'cidade', 'uf', 'is_matriz', 'participa_replicacao', 'ativo']
    list_filter = ['ativo', 'participa_replicacao', 'is_matriz', 'uf', 'empresa']
    search_fields = ['razao_social', 'nome_fantasia', 'cnpj']
    autocomplete_fields = ['empresa']


@admin.register(PoliticaReplicacao)
class PoliticaReplicacaoAdmin(admin.ModelAdmin):
    list_display = ['empresa', 'ativo', 'replicar_clientes', 'replicar_fornecedores', 'replicar_produtos_basicos']
    list_filter = ['ativo', 'replicar_clientes', 'replicar_fornecedores', 'replicar_produtos_basicos']
    autocomplete_fields = ['empresa']


@admin.register(PoliticaReplicacaoFilial)
class PoliticaReplicacaoFilialAdmin(admin.ModelAdmin):
    list_display = ['filial', 'empresa', 'ativo', 'replicar_clientes', 'replicar_fornecedores', 'replicar_produtos_basicos']
    list_filter = ['ativo', 'replicar_clientes', 'replicar_fornecedores', 'replicar_produtos_basicos', 'filial__empresa']
    autocomplete_fields = ['filial']

    @admin.display(ordering='filial__empresa', description='Empresa')
    def empresa(self, obj):
        return obj.filial.empresa


class PermissaoInline(admin.TabularInline):
    model = Permissao
    extra = 1


@admin.register(PerfilAcesso)
class PerfilAcessoAdmin(admin.ModelAdmin):
    list_display = ['nome', 'empresa', 'is_admin', 'ativo']
    list_filter = ['is_admin', 'ativo', 'empresa']
    search_fields = ['nome']
    inlines = [PermissaoInline]


@admin.register(Usuario)
class UsuarioAdmin(BaseUserAdmin):
    list_display = ['nome', 'email', 'empresa', 'filial', 'perfil', 'ativo']
    list_filter = ['ativo', 'empresa', 'perfil', 'is_superuser']
    search_fields = ['nome', 'email', 'cpf']
    ordering = ['nome']
    autocomplete_fields = ['empresa', 'filial', 'perfil']

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Identificação', {'fields': ('nome', 'cpf', 'telefone')}),
        ('Vínculo', {'fields': ('empresa', 'filial', 'perfil')}),
        ('Comercial', {'fields': ('comissao_percentual',)}),
        ('PDV', {'fields': ('pin_code', 'pin_exige_supervisor')}),
        ('Permissões', {'fields': ('ativo', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Auditoria', {'fields': ('ultimo_acesso', 'ip_ultimo_acesso', 'tentativas_login_falhas', 'bloqueado_ate'),
                       'classes': ('collapse',)}),
    )
    readonly_fields = ['ultimo_acesso', 'ip_ultimo_acesso']

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'nome', 'empresa', 'perfil', 'password1', 'password2'),
        }),
    )


@admin.register(SessaoUsuario)
class SessaoUsuarioAdmin(admin.ModelAdmin):
    list_display = ['usuario', 'filial', 'ip_acesso', 'device_serial', 'ativo', 'created_at']
    list_filter = ['ativo', 'filial']
    search_fields = ['usuario__nome', 'ip_acesso']
    readonly_fields = ['token_hash', 'refresh_token_hash']


@admin.register(UsuarioFilialAcesso)
class UsuarioFilialAcessoAdmin(admin.ModelAdmin):
    list_display = ['usuario', 'filial', 'perfil', 'ativo', 'is_padrao']
    list_filter = ['ativo', 'is_padrao', 'filial__empresa']
    search_fields = ['usuario__nome', 'usuario__email', 'filial__razao_social', 'perfil__nome']


@admin.register(LogSistema)
class LogSistemaAdmin(admin.ModelAdmin):
    list_display = ['data_hora', 'usuario', 'filial', 'modulo', 'acao', 'tabela_afetada', 'registro_id']
    list_filter = ['acao', 'modulo', 'filial']
    search_fields = ['usuario__nome', 'tabela_afetada']
    readonly_fields = [f.name for f in LogSistema._meta.fields]


@admin.register(RegistroAuditoria)
class RegistroAuditoriaAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'modulo', 'acao', 'objeto_tipo', 'objeto_id', 'usuario', 'filial')
    list_filter = ('modulo', 'acao', 'objeto_tipo', 'relacionado_tipo', 'criado_em')
    search_fields = ('objeto_descricao', 'justificativa', 'usuario__email', 'usuario__nome')
    readonly_fields = [f.name for f in RegistroAuditoria._meta.fields]
    date_hierarchy = 'criado_em'

    def has_add_permission(self, request):
        return False


@admin.register(LogAcesso)
class LogAcessoAdmin(admin.ModelAdmin):
    list_display = ['data_hora', 'usuario', 'tipo', 'ip_acesso', 'sucesso']
    list_filter = ['tipo', 'sucesso']
    search_fields = ['usuario__nome', 'ip_acesso']
    readonly_fields = [f.name for f in LogAcesso._meta.fields]
    date_hierarchy = 'data_hora'

    def has_add_permission(self, request):
        return False
