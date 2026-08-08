"""
Empresa (entidade raiz) e Filial (unidade operacional).
Mapeia as tabelas `empresas` e `filiais` do banco de referência.
"""
from django.core.validators import RegexValidator
from django.db import models

from .base import CoordenadaMixin, TimestampedModel


cnpj_validator = RegexValidator(r'^\d{14}$', 'CNPJ deve conter 14 dígitos numéricos.')
cep_validator = RegexValidator(r'^\d{8}$', 'CEP deve conter 8 dígitos numéricos.')


class Empresa(CoordenadaMixin, TimestampedModel):
    """Entidade jurídica raiz. Uma empresa pode ter várias filiais."""

    class RegimeTributario(models.TextChoices):
        SIMPLES_NACIONAL = 'simples_nacional', 'Simples Nacional'
        LUCRO_PRESUMIDO = 'lucro_presumido', 'Lucro Presumido'
        LUCRO_REAL = 'lucro_real', 'Lucro Real'
        MEI = 'mei', 'MEI'

    class CodigoRegimeTributario(models.IntegerChoices):
        SIMPLES_NACIONAL = 1, '1 - Simples Nacional'
        SIMPLES_EXCESSO_SUBLIMITE = 2, '2 - Simples Nacional com excesso de sublimite'
        REGIME_NORMAL = 3, '3 - Regime Normal'
        MEI = 4, '4 - Simples Nacional - MEI'

    class AmbienteNFe(models.IntegerChoices):
        PRODUCAO = 1, 'Produção'
        HOMOLOGACAO = 2, 'Homologação'

    razao_social = models.CharField(max_length=150)
    nome_fantasia = models.CharField(max_length=100, blank=True)
    cnpj = models.CharField(max_length=14, unique=True, validators=[cnpj_validator])
    inscricao_estadual = models.CharField(max_length=20, blank=True)
    inscricao_municipal = models.CharField(max_length=20, blank=True)

    regime_tributario = models.CharField(max_length=20, choices=RegimeTributario.choices)
    codigo_regime_tributario = models.SmallIntegerField(
        choices=CodigoRegimeTributario.choices,
        help_text='CRT do emitente: 1=SN, 2=SN excesso, 3=Normal, 4=MEI.',
    )

    # Endereço
    endereco = models.CharField(max_length=255, blank=True)
    numero = models.CharField(max_length=10, blank=True)
    complemento = models.CharField(max_length=60, blank=True)
    bairro = models.CharField(max_length=80, blank=True)
    cidade = models.CharField(max_length=80, blank=True)
    uf = models.CharField(max_length=2, blank=True)
    cep = models.CharField(max_length=8, blank=True)
    codigo_municipio_ibge = models.CharField(max_length=7, blank=True)

    telefone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(max_length=120, blank=True)
    site = models.URLField(max_length=120, blank=True)
    logo_url = models.URLField(max_length=500, blank=True)

    # Certificado digital
    certificado_digital_path = models.CharField(max_length=500, blank=True)
    certificado_senha_hash = models.CharField(max_length=255, blank=True)
    certificado_validade = models.DateField(null=True, blank=True)

    ambiente_nfe = models.SmallIntegerField(
        choices=AmbienteNFe.choices, default=AmbienteNFe.HOMOLOGACAO,
    )
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'empresas'
        ordering = ['razao_social']
        verbose_name = 'Empresa'
        verbose_name_plural = 'Empresas'

    def __str__(self):
        return self.nome_fantasia or self.razao_social


class Filial(CoordenadaMixin, TimestampedModel):
    """
    Unidade operacional da empresa. Todo dado transacional é escopado por filial.
    A filial marcada como `is_matriz=True` tem visão consolidada.
    """

    class AmbienteNFe(models.IntegerChoices):
        PRODUCAO = 1, 'Produção'
        HOMOLOGACAO = 2, 'Homologação'

    empresa = models.ForeignKey(
        Empresa, on_delete=models.PROTECT, related_name='filiais',
    )
    razao_social = models.CharField(max_length=150)
    nome_fantasia = models.CharField(max_length=100, blank=True)
    cnpj = models.CharField(max_length=14, unique=True, validators=[cnpj_validator])
    inscricao_estadual = models.CharField(max_length=20, blank=True)
    inscricao_municipal = models.CharField(max_length=20, blank=True)
    is_matriz = models.BooleanField(default=False)

    # Endereço
    endereco = models.CharField(max_length=255, blank=True)
    numero = models.CharField(max_length=10, blank=True)
    complemento = models.CharField(max_length=60, blank=True)
    bairro = models.CharField(max_length=80, blank=True)
    cidade = models.CharField(max_length=80, blank=True)
    uf = models.CharField(max_length=2)
    cep = models.CharField(max_length=8, blank=True)
    codigo_municipio_ibge = models.CharField(max_length=7, blank=True)

    telefone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(max_length=120, blank=True)
    imagem = models.ImageField(upload_to='filiais/imagens/', blank=True, null=True)

    # Fiscal por filial
    regime_tributario = models.CharField(
        max_length=20,
        choices=Empresa.RegimeTributario.choices,
        blank=True,
        help_text='Regime fiscal especifico da filial. Se vazio, usa o regime da empresa.',
    )
    codigo_regime_tributario = models.SmallIntegerField(
        null=True,
        blank=True,
        choices=Empresa.CodigoRegimeTributario.choices,
        help_text='CRT do emitente. Se vazio, usa o codigo da empresa.',
    )
    ambiente_nfe = models.SmallIntegerField(
        choices=AmbienteNFe.choices, default=AmbienteNFe.HOMOLOGACAO,
    )
    serie_nfe = models.SmallIntegerField(default=1)
    serie_nfce = models.SmallIntegerField(default=1)
    serie_nfse = models.SmallIntegerField(default=1)
    proximo_numero_nfe = models.BigIntegerField(default=1)
    proximo_numero_nfce = models.BigIntegerField(default=1)
    proximo_numero_nfse = models.BigIntegerField(default=1)

    # Focus NFe (cada filial tem seu token)
    focusnfe_token = models.CharField(max_length=100, blank=True)
    focusnfe_ambiente = models.SmallIntegerField(default=2)

    certificado_digital_path = models.CharField(max_length=500, blank=True)
    certificado_senha_hash = models.CharField(max_length=255, blank=True)
    certificado_validade = models.DateField(null=True, blank=True)

    ativo = models.BooleanField(default=True, db_index=True)
    participa_replicacao = models.BooleanField(
        default=True,
        db_index=True,
        help_text='Define se esta filial envia e recebe cadastros replicados.',
    )
    modulos_desativados = models.JSONField(
        default=list,
        blank=True,
        help_text='Chaves das secoes do menu desativadas nesta filial '
                   '(cadastros/operacoes/financeiro/logistica/avancado/food_service).',
    )

    class Meta:
        db_table = 'filiais'
        ordering = ['empresa', 'razao_social']
        verbose_name = 'Filial'
        verbose_name_plural = 'Filiais'
        indexes = [
            models.Index(fields=['empresa', 'ativo']),
        ]

    def __str__(self):
        nome = self.nome_fantasia or self.razao_social
        return f'{nome} ({self.cidade}/{self.uf})'


class PoliticaReplicacaoFilial(TimestampedModel):
    """Configura quais cadastros esta filial envia/recebe na replicacao."""

    filial = models.OneToOneField(
        Filial, on_delete=models.CASCADE, related_name='politica_replicacao',
    )
    replicar_clientes = models.BooleanField(default=False)
    replicar_fornecedores = models.BooleanField(default=False)
    replicar_produtos_basicos = models.BooleanField(default=False)
    replicar_categorias = models.BooleanField(default=False)
    replicar_marcas = models.BooleanField(default=False)
    replicar_unidades = models.BooleanField(default=False)
    replicar_tabelas_preco = models.BooleanField(default=False)
    replicar_preco_venda = models.BooleanField(default=False)
    replicar_custo_base = models.BooleanField(default=False)
    replicar_fiscal_basico = models.BooleanField(default=False)
    replicar_ficha_tecnica = models.BooleanField(default=False)
    replicar_qualidade = models.BooleanField(default=False)
    replicar_transportadoras = models.BooleanField(default=False)
    replicar_representantes = models.BooleanField(default=False)
    perguntar_ao_salvar = models.BooleanField(
        default=False,
        help_text='Reservado para permitir escolha manual por cadastro em etapa futura.',
    )
    ativo = models.BooleanField(default=True)

    class Meta:
        db_table = 'politicas_replicacao_filiais'
        verbose_name = 'Politica de Replicacao por Filial'
        verbose_name_plural = 'Politicas de Replicacao por Filial'

    def __str__(self):
        return f'Replicacao - {self.filial}'


class PoliticaReplicacao(TimestampedModel):
    """Politica legada por empresa, mantida como fallback para dados antigos."""

    empresa = models.OneToOneField(
        Empresa, on_delete=models.CASCADE, related_name='politica_replicacao',
    )
    replicar_clientes = models.BooleanField(default=False)
    replicar_fornecedores = models.BooleanField(default=False)
    replicar_produtos_basicos = models.BooleanField(default=False)
    replicar_categorias = models.BooleanField(default=False)
    replicar_marcas = models.BooleanField(default=False)
    replicar_unidades = models.BooleanField(default=False)
    replicar_tabelas_preco = models.BooleanField(default=False)
    replicar_preco_venda = models.BooleanField(default=False)
    replicar_custo_base = models.BooleanField(default=False)
    replicar_fiscal_basico = models.BooleanField(default=False)
    replicar_ficha_tecnica = models.BooleanField(default=False)
    replicar_qualidade = models.BooleanField(default=False)
    replicar_transportadoras = models.BooleanField(default=False)
    replicar_representantes = models.BooleanField(default=False)
    perguntar_ao_salvar = models.BooleanField(
        default=False,
        help_text='Reservado para permitir escolha manual por cadastro em etapa futura.',
    )
    ativo = models.BooleanField(default=True)

    class Meta:
        db_table = 'politicas_replicacao'
        verbose_name = 'PolÃ­tica de ReplicaÃ§Ã£o'
        verbose_name_plural = 'PolÃ­ticas de ReplicaÃ§Ã£o'

    def __str__(self):
        return f'ReplicaÃ§Ã£o - {self.empresa}'
