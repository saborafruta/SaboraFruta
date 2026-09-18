"""Registro central das autorizações locais do PDV."""
from django.db import models
from django.utils import timezone


class InstalacaoPDVOffline(models.Model):
    class Status(models.TextChoices):
        ATIVA = "ativa", "Ativa"
        INATIVA = "inativa", "Desativada no PDV"
        REDEFINIR = "redefinir", "Aguardando novo PIN"
        REVOGADA = "revogada", "Revogada"

    tenant_alias = models.CharField(max_length=100, blank=True, db_index=True)
    empresa_id_origem = models.BigIntegerField(null=True, blank=True, db_index=True)
    filial_id_origem = models.BigIntegerField(db_index=True)
    filial_nome = models.CharField(max_length=160)
    usuario_id_origem = models.BigIntegerField(db_index=True)
    usuario_nome = models.CharField(max_length=160)
    usuario_login = models.CharField(max_length=254, blank=True)
    installation_id = models.CharField(max_length=80, db_index=True)
    nome_dispositivo = models.CharField(max_length=120)
    caixa_id_origem = models.BigIntegerField(null=True, blank=True)
    caixa_descricao = models.CharField(max_length=120, blank=True)
    user_agent = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ATIVA, db_index=True)
    revisao = models.PositiveIntegerField(default=1)
    recuperacao_configurada = models.BooleanField(default=False)
    fila_pendente_quantidade = models.PositiveIntegerField(default=0)
    fila_erro_quantidade = models.PositiveIntegerField(default=0)
    catalogo_atualizado_em = models.DateTimeField(null=True, blank=True)
    ultima_sincronizacao_em = models.DateTimeField(null=True, blank=True)
    ultimo_backup_em = models.DateTimeField(null=True, blank=True)
    ultimo_erro_sincronizacao = models.TextField(blank=True)
    autorizado_em = models.DateTimeField(auto_now_add=True)
    visto_por_ultimo_em = models.DateTimeField(auto_now=True, db_index=True)
    revogado_em = models.DateTimeField(null=True, blank=True)
    revogado_por_id = models.BigIntegerField(null=True, blank=True)
    revogado_por_nome = models.CharField(max_length=160, blank=True)
    motivo_revogacao = models.TextField(blank=True)

    class Meta:
        db_table = "pdv_instalacoes_offline"
        ordering = ["-visto_por_ultimo_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_alias", "filial_id_origem", "usuario_id_origem", "installation_id"],
                name="uniq_pdv_offline_escopo_instalacao",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "-visto_por_ultimo_em"], name="pdv_off_status_visto_idx"),
            models.Index(fields=["fila_pendente_quantidade", "-visto_por_ultimo_em"], name="pdv_off_fila_visto_idx"),
        ]

    def __str__(self):
        return f"{self.nome_dispositivo} - {self.filial_nome} - {self.usuario_nome}"

    @property
    def catalogo_vencido(self):
        if not self.catalogo_atualizado_em:
            return True
        return self.catalogo_atualizado_em < timezone.now() - timezone.timedelta(hours=12)

    @property
    def contato_atrasado(self):
        return self.visto_por_ultimo_em < timezone.now() - timezone.timedelta(minutes=10)


class EventoInstalacaoPDVOffline(models.Model):
    instalacao = models.ForeignKey(
        InstalacaoPDVOffline,
        on_delete=models.CASCADE,
        related_name="eventos",
    )
    tipo = models.CharField(max_length=30, db_index=True)
    ator_id = models.BigIntegerField(null=True, blank=True)
    ator_nome = models.CharField(max_length=160, blank=True)
    detalhe = models.TextField(blank=True)
    metadados = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "pdv_instalacoes_offline_eventos"
        ordering = ["-criado_em"]
