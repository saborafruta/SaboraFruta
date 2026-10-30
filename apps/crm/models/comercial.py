"""Fundação do CRM comercial: funis, oportunidades, tags e histórico auditável."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models.base import FilialScopedModel, TimestampedModel


class CarteiraCliente(TimestampedModel):
    """Vincula um cliente da empresa ao vendedor responsável por atendê-lo."""

    empresa = models.ForeignKey(
        'core.Empresa', on_delete=models.CASCADE, related_name='carteiras_clientes',
    )
    cliente = models.OneToOneField(
        'cadastros.Cliente', on_delete=models.CASCADE, related_name='carteira_crm',
    )
    grupo_cliente = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    vendedor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='clientes_carteira_crm',
    )
    atribuido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='atribuicoes_carteira_crm',
    )

    class Meta:
        db_table = 'crm_carteiras_clientes'
        ordering = ['cliente__razao_social']
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'grupo_cliente'],
                name='crm_carteira_empresa_grupo_cliente_unico',
            ),
        ]
        indexes = [
            models.Index(fields=['empresa', 'vendedor']),
        ]

    def clean(self):
        super().clean()
        erros = {}
        if self.cliente_id:
            empresa_cliente_id = self.cliente.filial.empresa_id
            if self.empresa_id and empresa_cliente_id != self.empresa_id:
                erros['cliente'] = 'O cliente pertence a outra empresa.'
        if self.vendedor_id and self.empresa_id:
            if self.vendedor.empresa_id != self.empresa_id:
                erros['vendedor'] = 'O vendedor pertence a outra empresa.'
        if self.atribuido_por_id and self.empresa_id:
            if self.atribuido_por.empresa_id != self.empresa_id:
                erros['atribuido_por'] = 'O responsável pela atribuição pertence a outra empresa.'
        if erros:
            raise ValidationError(erros)

    def save(self, *args, **kwargs):
        if self.cliente_id:
            self.empresa_id = self.cliente.filial.empresa_id
            self.grupo_cliente = self.cliente.grupo_replicacao
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.cliente} — {self.vendedor}'


class PipelineCRM(FilialScopedModel):
    nome = models.CharField(max_length=100)
    descricao = models.TextField(blank=True)
    padrao = models.BooleanField(default=False, db_index=True)
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'crm_pipelines'
        ordering = ['nome']
        constraints = [
            models.UniqueConstraint(
                fields=['filial', 'nome'], name='crm_pipeline_nome_filial_unico',
            ),
            models.UniqueConstraint(
                fields=['filial'], condition=Q(padrao=True),
                name='crm_pipeline_padrao_filial_unico',
            ),
        ]

    def __str__(self):
        return self.nome


class EtapaPipelineCRM(TimestampedModel):
    class Tipo(models.TextChoices):
        ABERTA = 'aberta', 'Em andamento'
        GANHA = 'ganha', 'Ganha'
        PERDIDA = 'perdida', 'Perdida'

    pipeline = models.ForeignKey(
        PipelineCRM, on_delete=models.CASCADE, related_name='etapas',
    )
    nome = models.CharField(max_length=80)
    cor = models.CharField(max_length=7, default='#2563eb')
    ordem = models.PositiveSmallIntegerField(default=0)
    tipo = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.ABERTA)
    probabilidade = models.PositiveSmallIntegerField(default=0)
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'crm_pipeline_etapas'
        ordering = ['ordem', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['pipeline', 'nome'], name='crm_etapa_nome_pipeline_unico',
            ),
            models.UniqueConstraint(
                fields=['pipeline', 'ordem'], name='crm_etapa_ordem_pipeline_unica',
            ),
            models.CheckConstraint(
                condition=Q(probabilidade__gte=0, probabilidade__lte=100),
                name='crm_etapa_probabilidade_valida',
            ),
        ]

    def __str__(self):
        return f'{self.pipeline} — {self.nome}'


class TagCRM(FilialScopedModel):
    nome = models.CharField(max_length=60)
    cor = models.CharField(max_length=7, default='#64748b')
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'crm_tags'
        ordering = ['nome']
        constraints = [
            models.UniqueConstraint(
                fields=['filial', 'nome'], name='crm_tag_nome_filial_unico',
            ),
        ]

    def __str__(self):
        return self.nome


class OportunidadeCRM(FilialScopedModel):
    class Prioridade(models.TextChoices):
        BAIXA = 'baixa', 'Baixa'
        NORMAL = 'normal', 'Normal'
        ALTA = 'alta', 'Alta'
        URGENTE = 'urgente', 'Urgente'

    class Origem(models.TextChoices):
        MANUAL = 'manual', 'Cadastro manual'
        WHATSAPP = 'whatsapp', 'WhatsApp'
        RECOMPRA = 'recompra', 'Alerta de recompra'
        ORCAMENTO = 'orcamento', 'Orçamento'
        SITE = 'site', 'Site ou formulário'
        INDICACAO = 'indicacao', 'Indicação'
        OUTRA = 'outra', 'Outra'

    cliente = models.ForeignKey(
        'cadastros.Cliente', on_delete=models.PROTECT, related_name='oportunidades_crm',
    )
    pipeline = models.ForeignKey(
        PipelineCRM, on_delete=models.PROTECT, related_name='oportunidades',
    )
    etapa = models.ForeignKey(
        EtapaPipelineCRM, on_delete=models.PROTECT, related_name='oportunidades',
    )
    titulo = models.CharField(max_length=160)
    descricao = models.TextField(blank=True)
    valor_estimado = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    prioridade = models.CharField(
        max_length=10, choices=Prioridade.choices, default=Prioridade.NORMAL, db_index=True,
    )
    origem = models.CharField(
        max_length=20, choices=Origem.choices, default=Origem.MANUAL, db_index=True,
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='oportunidades_crm',
    )
    tags = models.ManyToManyField(TagCRM, blank=True, related_name='oportunidades')
    previsao_fechamento = models.DateField(null=True, blank=True)
    ultimo_contato_em = models.DateTimeField(null=True, blank=True)
    proxima_acao = models.CharField(max_length=180, blank=True)
    proxima_acao_em = models.DateTimeField(null=True, blank=True, db_index=True)
    motivo_perda = models.TextField(blank=True)
    conversa_whatsapp_id = models.PositiveBigIntegerField(
        null=True, blank=True, db_index=True,
        help_text=(
            'Referência sem chave estrangeira: as conversas vivem no Banco Gerencial '
            'e a oportunidade vive no banco operacional da empresa.'
        ),
    )
    fechado_em = models.DateTimeField(null=True, blank=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='oportunidades_criadas',
    )

    class Meta:
        db_table = 'crm_oportunidades'
        ordering = ['etapa__ordem', '-prioridade', '-updated_at']
        indexes = [
            models.Index(fields=['filial', 'pipeline', 'etapa']),
            models.Index(fields=['filial', 'responsavel', 'proxima_acao_em']),
            models.Index(fields=['cliente', '-updated_at']),
        ]

    def clean(self):
        super().clean()
        erros = {}
        if self.etapa_id and self.pipeline_id and self.etapa.pipeline_id != self.pipeline_id:
            erros['etapa'] = 'A etapa selecionada não pertence a este funil.'
        if self.pipeline_id and self.filial_id and self.pipeline.filial_id != self.filial_id:
            erros['pipeline'] = 'O funil selecionado pertence a outra filial.'
        if self.etapa_id and self.filial_id and self.etapa.pipeline.filial_id != self.filial_id:
            erros['etapa'] = 'A etapa selecionada pertence a outra filial.'
        if self.responsavel_id and self.filial_id:
            if self.responsavel.empresa_id != self.filial.empresa_id:
                erros['responsavel'] = 'O responsável pertence a outra empresa.'
        if erros:
            raise ValidationError(erros)

    @property
    def encerrada(self):
        return self.etapa.tipo in {
            EtapaPipelineCRM.Tipo.GANHA, EtapaPipelineCRM.Tipo.PERDIDA,
        }

    def __str__(self):
        return self.titulo


class AtividadeCRM(TimestampedModel):
    class Tipo(models.TextChoices):
        CRIACAO = 'criacao', 'Oportunidade criada'
        EDICAO = 'edicao', 'Dados atualizados'
        MOVIMENTACAO = 'movimentacao', 'Mudança de etapa'
        NOTA = 'nota', 'Nota'
        CONTATO = 'contato', 'Contato realizado'
        SISTEMA = 'sistema', 'Evento do sistema'

    oportunidade = models.ForeignKey(
        OportunidadeCRM, on_delete=models.CASCADE, related_name='atividades',
    )
    tipo = models.CharField(max_length=20, choices=Tipo.choices, db_index=True)
    descricao = models.TextField()
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='atividades_crm',
    )
    metadados = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'crm_atividades'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.oportunidade}: {self.get_tipo_display()}'
