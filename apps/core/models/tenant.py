"""Diretório central dos bancos dedicados por empresa."""
import os

from django.core.exceptions import ValidationError
from django.db import models

from .base import TimestampedModel


class RailwayProjectPool(TimestampedModel):
    """Projeto Railway disponível para hospedar bancos dedicados de empresas.

    Segredos nunca são persistidos aqui. ``token_env_var`` guarda somente o
    nome da variável protegida existente no app central.
    """

    class Status(models.TextChoices):
        ATIVO = 'ativo', 'Ativo'
        LOTADO = 'lotado', 'Lotado'
        MANUTENCAO = 'manutencao', 'Manutenção'
        ERRO = 'erro', 'Erro'

    class ConnectionMode(models.TextChoices):
        PRIVATE = 'private', 'Rede privada (mesmo projeto)'
        PUBLIC = 'public', 'TCP Proxy com SSL (outro projeto)'

    nome = models.CharField(max_length=120, unique=True)
    railway_project_id = models.CharField(max_length=120, unique=True)
    railway_environment_id = models.CharField(max_length=120)
    token_env_var = models.CharField(
        max_length=120,
        help_text='Nome da variável protegida que contém o Project Token deste projeto.',
    )
    connection_mode = models.CharField(
        max_length=16, choices=ConnectionMode.choices, default=ConnectionMode.PUBLIC,
    )
    prioridade = models.PositiveSmallIntegerField(default=100, db_index=True)
    max_volumes = models.PositiveSmallIntegerField(default=10)
    volumes_reservados = models.PositiveSmallIntegerField(
        default=1,
        help_text='Vagas preservadas para restauração ou manutenção emergencial.',
    )
    ultimo_total_volumes = models.PositiveSmallIntegerField(null=True, blank=True)
    ultima_verificacao_em = models.DateTimeField(null=True, blank=True)
    ultimo_erro = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.MANUTENCAO, db_index=True,
    )
    ativo = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = 'railway_project_pools'
        ordering = ['prioridade', 'nome']
        verbose_name = 'Projeto Railway de bancos'
        verbose_name_plural = 'Projetos Railway de bancos'

    def clean(self):
        super().clean()
        if self.max_volumes < 1:
            raise ValidationError({'max_volumes': 'Informe ao menos um volume.'})
        if self.volumes_reservados >= self.max_volumes:
            raise ValidationError({
                'volumes_reservados': 'A reserva deve ser menor que o limite de volumes.',
            })
        if self.connection_mode == self.ConnectionMode.PRIVATE:
            from django.conf import settings

            current_project_id = getattr(settings, 'RAILWAY_PROJECT_ID', '')
            if current_project_id and self.railway_project_id != current_project_id:
                raise ValidationError({
                    'connection_mode': (
                        'Rede privada só pode ser usada pelo projeto do app central.'
                    ),
                })

    @property
    def capacidade_operacional(self):
        return max(self.max_volumes - self.volumes_reservados, 0)

    @property
    def vagas_estimadas(self):
        usados = self.ultimo_total_volumes or 0
        return max(self.capacidade_operacional - usados, 0)

    @property
    def token_configured(self):
        return bool(os.environ.get(self.token_env_var, '').strip())

    def __str__(self):
        return self.nome


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
    railway_volume_id = models.CharField(max_length=120, blank=True)
    railway_tcp_proxy_id = models.CharField(max_length=120, blank=True)
    railway_tcp_proxy_domain = models.CharField(max_length=255, blank=True)
    railway_tcp_proxy_port = models.PositiveIntegerField(null=True, blank=True)
    railway_project_pool = models.ForeignKey(
        RailwayProjectPool,
        on_delete=models.PROTECT,
        related_name='bancos',
        null=True,
        blank=True,
    )
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
