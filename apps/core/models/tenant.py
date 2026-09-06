"""Diretório central dos bancos dedicados por empresa."""
from django.db import models

from .base import TimestampedModel


class EmpresaBanco(TimestampedModel):
    class Status(models.TextChoices):
        PENDENTE = 'pendente', 'Pendente'
        AGUARDANDO_CONFIGURACAO = 'aguardando_configuracao', 'Aguardando configuração'
        CONFIGURADO = 'configurado', 'Configurado'
        ATIVO = 'ativo', 'Ativo'
        ERRO = 'erro', 'Erro'
        INATIVO = 'inativo', 'Inativo'

    empresa = models.OneToOneField(
        'core.Empresa', on_delete=models.CASCADE, related_name='banco_dedicado',
    )
    slug = models.SlugField(max_length=80, unique=True)
    db_alias = models.SlugField(max_length=80, unique=True)
    database_url_env_var = models.CharField(max_length=120, blank=True)
    railway_database_service_id = models.CharField(max_length=120, blank=True)
    railway_database_service_name = models.CharField(max_length=120, blank=True)
    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.PENDENTE, db_index=True,
    )
    provisionamento_modo = models.CharField(max_length=30, default='manual')
    provisionamento_solicitado_em = models.DateTimeField(null=True, blank=True)
    provisionado_em = models.DateTimeField(null=True, blank=True)
    ultima_migracao_em = models.DateTimeField(null=True, blank=True)
    ultima_verificacao_em = models.DateTimeField(null=True, blank=True)
    ultimo_erro = models.TextField(blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'empresas_bancos'
        ordering = ['empresa__razao_social']
        verbose_name = 'Banco da empresa'
        verbose_name_plural = 'Bancos das empresas'

    @property
    def nome_banco(self):
        return self.railway_database_service_name or self.db_alias

    def __str__(self):
        return f'{self.empresa} - {self.nome_banco}'


class TenantPublicLink(TimestampedModel):
    """Índice central, sem guardar o token público em texto aberto."""

    tipo = models.CharField(max_length=24)
    token_hash = models.CharField(max_length=64)
    db_alias = models.SlugField(max_length=80, db_index=True)

    class Meta:
        db_table = 'tenant_public_links'
        constraints = [
            models.UniqueConstraint(
                fields=['tipo', 'token_hash'], name='uq_tenant_public_link_tipo_hash',
            ),
        ]

    def __str__(self):
        return f'{self.tipo}: {self.db_alias}'
