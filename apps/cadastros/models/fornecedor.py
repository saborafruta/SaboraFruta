"""Fornecedor com avaliação de qualidade."""
import uuid

from django.db import models

from apps.core.constants.choices import TipoPessoa, UF
from apps.core.models.base import CoordenadaMixin, FilialManager, FilialScopedModel, TimestampedModel


class FornecedorManager(FilialManager):
    def for_filial(self, filial):
        if filial is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(filiais_vinculo__filial=filial, filiais_vinculo__ativo=True).distinct()

    def for_empresa(self, empresa):
        if empresa is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(filiais_vinculo__filial__empresa=empresa, filiais_vinculo__ativo=True).distinct()


class Fornecedor(CoordenadaMixin, FilialScopedModel):
    """Mapeia `fornecedores` do banco de referência."""

    tipo_pessoa = models.CharField(max_length=1, choices=TipoPessoa.choices)
    razao_social = models.CharField(max_length=150)
    nome_fantasia = models.CharField(max_length=100, blank=True)
    cpf_cnpj = models.CharField(max_length=14, blank=True, db_index=True)
    inscricao_estadual = models.CharField(max_length=20, blank=True)
    inscricao_municipal = models.CharField(max_length=20, blank=True)

    # Endereço
    endereco = models.CharField(max_length=255, blank=True)
    numero = models.CharField(max_length=10, blank=True)
    complemento = models.CharField(max_length=60, blank=True)
    bairro = models.CharField(max_length=80, blank=True)
    cidade = models.CharField(max_length=80, blank=True)
    uf = models.CharField(max_length=2, choices=UF.choices, blank=True)
    cep = models.CharField(max_length=8, blank=True)
    codigo_municipio_ibge = models.CharField(max_length=7, blank=True)
    pais = models.CharField(max_length=60, default='Brasil')
    codigo_pais_bacen = models.CharField(max_length=4, default='1058')

    # Contato
    telefone = models.CharField(max_length=20, blank=True)
    celular = models.CharField(max_length=20, blank=True)
    email = models.EmailField(max_length=120, blank=True)
    contato_nome = models.CharField(max_length=100, blank=True)

    # Comercial
    prazo_entrega_dias = models.SmallIntegerField(default=0)
    contribuinte_icms = models.BooleanField(default=True)
    optante_simples = models.BooleanField(default=False)

    # Avaliação de qualidade (estrelas 1-5)
    nota_qualidade = models.DecimalField(
        max_digits=3, decimal_places=2, default=0,
        help_text='Nota média 0.00-5.00 calculada automaticamente no histórico de entregas',
    )
    total_entregas = models.IntegerField(default=0)
    entregas_no_prazo = models.IntegerField(default=0)

    id_externo = models.CharField(max_length=100, blank=True, db_index=True)
    grupo_replicacao = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    observacao = models.TextField(blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    objects = FornecedorManager()

    class Meta:
        db_table = 'fornecedores'
        ordering = ['razao_social']
        verbose_name = 'Fornecedor'
        indexes = [
            models.Index(fields=['filial', 'ativo']),
            models.Index(fields=['filial', 'cpf_cnpj']),
        ]

    @property
    def percentual_no_prazo(self):
        if self.total_entregas == 0:
            return 0
        return round((self.entregas_no_prazo / self.total_entregas) * 100, 2)

    def __str__(self):
        return self.nome_fantasia or self.razao_social

    @property
    def codigo_compartilhado(self):
        if not self.pk or not self.grupo_replicacao:
            return self.pk
        return self.__class__.objects.filter(
            grupo_replicacao=self.grupo_replicacao,
            filial__empresa_id=self.filial.empresa_id,
        ).order_by('pk').values_list('pk', flat=True).first() or self.pk


class FornecedorFilial(TimestampedModel):
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.CASCADE, related_name='filiais_vinculo')
    filial = models.ForeignKey('core.Filial', on_delete=models.CASCADE, related_name='fornecedores_vinculados')
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'fornecedores_filiais'
        ordering = ['fornecedor', 'filial']
        unique_together = [('fornecedor', 'filial')]
        indexes = [
            models.Index(fields=['filial', 'ativo']),
            models.Index(fields=['fornecedor', 'ativo']),
        ]

    def __str__(self):
        return f'{self.fornecedor} - {self.filial}'

    @property
    def percentual_no_prazo(self):
        if self.total_entregas == 0:
            return 0
        return round((self.entregas_no_prazo / self.total_entregas) * 100, 2)
