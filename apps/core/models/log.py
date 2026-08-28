"""Auditoria: log de acesso e log de alterações."""
from django.db import models


class LogSistema(models.Model):
    """
    Auditoria de operações CRUD. Preenchido automaticamente pelo AuditMiddleware
    via signals post_save/post_delete.
    """

    class Acao(models.TextChoices):
        CRIAR = 'criar', 'Criar'
        EDITAR = 'editar', 'Editar'
        EXCLUIR = 'excluir', 'Excluir'
        CANCELAR = 'cancelar', 'Cancelar'
        APROVAR = 'aprovar', 'Aprovar'
        REJEITAR = 'rejeitar', 'Rejeitar'
        ACESSAR = 'acessar', 'Acessar'
        EXPORTAR = 'exportar', 'Exportar'

    filial = models.ForeignKey(
        'core.Filial', on_delete=models.SET_NULL, null=True, blank=True,
    )
    usuario = models.ForeignKey(
        'core.Usuario', on_delete=models.SET_NULL, null=True, blank=True,
    )
    modulo = models.CharField(max_length=60)
    acao = models.CharField(max_length=30, choices=Acao.choices)
    tabela_afetada = models.CharField(max_length=60, blank=True)
    registro_id = models.BigIntegerField(null=True, blank=True)
    dados_anteriores = models.JSONField(null=True, blank=True)
    dados_novos = models.JSONField(null=True, blank=True)
    ip_acesso = models.GenericIPAddressField(null=True, blank=True)
    device_serial = models.CharField(max_length=255, blank=True)
    user_agent = models.TextField(blank=True)
    data_hora = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'log_sistema'
        ordering = ['-data_hora']
        indexes = [
            models.Index(fields=['tabela_afetada', 'registro_id']),
            models.Index(fields=['usuario', '-data_hora']),
        ]


class LogAcesso(models.Model):
    """Auditoria de login, logout, bloqueios e tentativas."""

    class Tipo(models.TextChoices):
        LOGIN = 'login', 'Login'
        LOGOUT = 'logout', 'Logout'
        SENHA_ERRADA = 'senha_errada', 'Senha Errada'
        BLOQUEIO = 'bloqueio', 'Bloqueio'
        PIN_PDV = 'pin_pdv', 'PIN PDV'

    usuario = models.ForeignKey('core.Usuario', on_delete=models.CASCADE)
    filial = models.ForeignKey(
        'core.Filial', on_delete=models.SET_NULL, null=True, blank=True,
    )
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    ip_acesso = models.GenericIPAddressField(null=True, blank=True)
    device_serial = models.CharField(max_length=255, blank=True)
    user_agent = models.TextField(blank=True)
    sucesso = models.BooleanField()
    data_hora = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'log_acesso'
        ordering = ['-data_hora']


class RegistroAuditoria(models.Model):
    """Registro operacional explicito para acoes sensiveis do ERP."""

    class Modulo(models.TextChoices):
        COMPRAS = 'compras', 'Compras'
        ESTOQUE = 'estoque', 'Estoque'
        FINANCEIRO = 'financeiro', 'Financeiro'
        LOGISTICA = 'logistica', 'Logística'

    class Acao(models.TextChoices):
        VISUALIZAR = 'visualizar', 'Visualizar'
        CRIAR = 'criar', 'Criar'
        EDITAR = 'editar', 'Editar'
        APROVAR = 'aprovar', 'Aprovar'
        CANCELAR = 'cancelar', 'Cancelar'
        EXPORTAR = 'exportar', 'Exportar'
        EFETIVAR = 'efetivar', 'Efetivar'
        VINCULAR = 'vincular', 'Vincular'
        REPROCESSAR = 'reprocessar', 'Reprocessar'
        AJUSTAR = 'ajustar', 'Ajustar'
        TRANSFERIR = 'transferir', 'Transferir'
        INVENTARIAR = 'inventariar', 'Inventariar'
        BAIXAR_VALIDADE = 'baixar_validade', 'Baixar validade'
        EXCLUIR = 'excluir', 'Excluir'
        RESTAURAR = 'restaurar', 'Restaurar'

    filial = models.ForeignKey(
        'core.Filial', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='registros_auditoria',
    )
    usuario = models.ForeignKey(
        'core.Usuario', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='registros_auditoria',
    )
    modulo = models.CharField(max_length=40, choices=Modulo.choices, db_index=True)
    acao = models.CharField(max_length=40, choices=Acao.choices, db_index=True)
    objeto_tipo = models.CharField(max_length=80, db_index=True)
    objeto_id = models.BigIntegerField(db_index=True)
    objeto_descricao = models.CharField(max_length=255, blank=True)
    relacionado_tipo = models.CharField(max_length=80, blank=True, db_index=True)
    relacionado_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    justificativa = models.TextField(blank=True)
    dados_anteriores = models.JSONField(null=True, blank=True)
    dados_novos = models.JSONField(null=True, blank=True)
    metadados = models.JSONField(null=True, blank=True)
    ip_acesso = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'registros_auditoria'
        ordering = ['-criado_em']
        indexes = [
            models.Index(fields=['modulo', 'acao', '-criado_em']),
            models.Index(fields=['objeto_tipo', 'objeto_id', '-criado_em']),
            models.Index(fields=['relacionado_tipo', 'relacionado_id', '-criado_em']),
            models.Index(fields=['usuario', '-criado_em']),
            models.Index(fields=['filial', '-criado_em']),
        ]
        verbose_name = 'Registro de auditoria'
        verbose_name_plural = 'Registros de auditoria'

    def __str__(self):
        return f'{self.get_modulo_display()} {self.get_acao_display()} {self.objeto_tipo}#{self.objeto_id}'
