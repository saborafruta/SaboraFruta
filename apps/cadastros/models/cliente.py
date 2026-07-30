"""Cliente — integração com ViaCEP, classificação estrelas, histórico."""
import uuid

from django.db import models

from apps.core.constants.choices import TipoPessoa, UF
from apps.core.models.base import CoordenadaMixin, FilialManager, FilialScopedModel, TimestampedModel


class ClienteManager(FilialManager):
    def for_filial(self, filial):
        if filial is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(filiais_vinculo__filial=filial, filiais_vinculo__ativo=True).distinct()

    def for_empresa(self, empresa):
        if empresa is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(filiais_vinculo__filial__empresa=empresa, filiais_vinculo__ativo=True).distinct()


class Cliente(CoordenadaMixin, FilialScopedModel):
    """Mapeia `clientes` do banco de referência."""

    class Tipo(models.TextChoices):
        VAREJO = 'varejo', 'Varejo'
        ATACADO = 'atacado', 'Atacado'
        DISTRIBUIDOR = 'distribuidor', 'Distribuidor'

    tipo_pessoa = models.CharField(max_length=1, choices=TipoPessoa.choices)
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.VAREJO)

    razao_social = models.CharField(max_length=150)
    nome_fantasia = models.CharField(max_length=100, blank=True)
    cpf_cnpj = models.CharField(max_length=14, blank=True, db_index=True)
    rg_ie = models.CharField(max_length=20, blank=True)
    inscricao_municipal = models.CharField(max_length=20, blank=True)
    inscricao_estadual = models.CharField(max_length=20, blank=True)

    # Pessoa física
    data_nascimento = models.DateField(null=True, blank=True)
    sexo = models.CharField(max_length=1, blank=True)

    # Endereço principal
    endereco = models.CharField(max_length=255, blank=True)
    numero = models.CharField(max_length=10, blank=True)
    complemento = models.CharField(max_length=60, blank=True)
    bairro = models.CharField(max_length=80, blank=True)
    cidade = models.CharField(max_length=80, blank=True)
    uf = models.CharField(max_length=2, choices=UF.choices, blank=True)
    cep = models.CharField(max_length=8, blank=True)
    codigo_municipio_ibge = models.CharField(max_length=7, blank=True)
    pais = models.CharField(max_length=60, blank=True, default='Brasil')
    codigo_pais_bacen = models.CharField(max_length=4, blank=True, default='1058')
    id_estrangeiro = models.CharField(max_length=20, blank=True)

    # Contato
    telefone = models.CharField(max_length=20, blank=True)
    celular = models.CharField(max_length=20, blank=True)
    email = models.EmailField(max_length=120, blank=True)
    email_nfe = models.EmailField(max_length=120, blank=True)
    contato_nome = models.CharField(max_length=100, blank=True)

    # Comercial
    limite_credito = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    saldo_devedor = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text='Calculado. Atualizado via signals quando contas a receber mudam.',
    )
    prazo_pagamento_dias = models.SmallIntegerField(default=0)
    tabela_preco = models.ForeignKey(
        'produtos.TabelaPreco',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='clientes',
        help_text='Quando vazia, usa o preco padrao cadastrado no produto.',
    )
    grupo_desconto = models.CharField(
        max_length=50, blank=True,
        help_text='Agrupamento para regras de desconto e tabela de preço.',
    )

    # Indicadores
    bloqueado = models.BooleanField(default=False)
    motivo_bloqueio = models.TextField(blank=True)
    consumidor_final = models.BooleanField(default=True, help_text='Impacta DIFAL em interestaduais')
    contribuinte_icms = models.BooleanField(default=False)
    optante_simples = models.BooleanField(default=False)

    # Fidelidade / Classificação
    pontos_fidelidade = models.IntegerField(default=0)

    id_externo = models.CharField(max_length=100, blank=True, db_index=True)
    grupo_replicacao = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    observacao = models.TextField(blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    objects = ClienteManager()

    class Meta:
        db_table = 'clientes'
        ordering = ['razao_social']
        verbose_name = 'Cliente'
        indexes = [
            models.Index(fields=['filial', 'ativo']),
            models.Index(fields=['filial', 'cpf_cnpj']),
            models.Index(fields=['filial', 'razao_social']),
        ]

    def __str__(self):
        return self.nome_fantasia or self.razao_social

    @property
    def nome_display(self):
        return self.nome_fantasia or self.razao_social

    @property
    def codigo_compartilhado(self):
        if not self.pk or not self.grupo_replicacao:
            return self.pk
        return self.__class__.objects.filter(
            grupo_replicacao=self.grupo_replicacao,
            filial__empresa_id=self.filial.empresa_id,
        ).order_by('pk').values_list('pk', flat=True).first() or self.pk


class ClienteFilial(TimestampedModel):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='filiais_vinculo')
    filial = models.ForeignKey('core.Filial', on_delete=models.CASCADE, related_name='clientes_vinculados')
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'clientes_filiais'
        ordering = ['cliente', 'filial']
        unique_together = [('cliente', 'filial')]
        indexes = [
            models.Index(fields=['filial', 'ativo']),
            models.Index(fields=['cliente', 'ativo']),
        ]

    def __str__(self):
        return f'{self.cliente} - {self.filial}'


class ClienteEndereco(CoordenadaMixin, TimestampedModel):
    """Endereços adicionais do cliente (entrega, cobrança, filiais)."""

    class Tipo(models.TextChoices):
        ENTREGA = 'entrega', 'Entrega'
        COBRANCA = 'cobranca', 'Cobrança'
        FILIAL_CLIENTE = 'filial_cliente', 'Filial Cliente'

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='enderecos')
    tipo = models.CharField(max_length=30, choices=Tipo.choices)
    descricao = models.CharField(max_length=80, blank=True)
    endereco = models.CharField(max_length=255)
    numero = models.CharField(max_length=10, blank=True)
    complemento = models.CharField(max_length=60, blank=True)
    bairro = models.CharField(max_length=80, blank=True)
    cidade = models.CharField(max_length=80, blank=True)
    uf = models.CharField(max_length=2, choices=UF.choices)
    cep = models.CharField(max_length=8)
    codigo_municipio_ibge = models.CharField(max_length=7, blank=True)
    pais = models.CharField(max_length=60, default='Brasil')
    codigo_pais_bacen = models.CharField(max_length=4, default='1058')
    padrao = models.BooleanField(default=False)
    ativo = models.BooleanField(default=True)

    class Meta:
        db_table = 'clientes_enderecos'
        ordering = ['cliente', 'tipo']
