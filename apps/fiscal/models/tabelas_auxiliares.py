"""Auxiliary fiscal reference tables used to suggest tax data."""
from django.db import models

from apps.core.constants.choices import UF
from apps.core.models.base import TimestampedModel


class TabelaFiscalAuxiliar(TimestampedModel):
    """Versioned internal reference item for NCM, CEST, CFOP, TIPI/IPI and CST."""

    class Tipo(models.TextChoices):
        NCM = 'ncm', 'NCM'
        CEST = 'cest', 'CEST'
        CFOP = 'cfop', 'CFOP'
        IPI_TIPI = 'ipi_tipi', 'IPI/TIPI'
        CST_PIS_COFINS = 'cst_pis_cofins', 'CST PIS/COFINS'

    tipo = models.CharField(max_length=20, choices=Tipo.choices, db_index=True)
    codigo = models.CharField(max_length=20, db_index=True)
    descricao = models.CharField(max_length=255)
    ncm = models.CharField(max_length=8, blank=True, db_index=True)
    cest = models.CharField(max_length=7, blank=True, db_index=True)
    uf = models.CharField(max_length=2, choices=UF.choices, blank=True, db_index=True)
    aliquota = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    fonte = models.CharField(max_length=120, blank=True)
    versao = models.CharField(max_length=40, blank=True)
    vigencia_inicio = models.DateField(null=True, blank=True)
    vigencia_fim = models.DateField(null=True, blank=True)
    observacao = models.TextField(blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'tabelas_fiscais_auxiliares'
        ordering = ['tipo', 'codigo', 'uf']
        indexes = [
            models.Index(fields=['tipo', 'codigo', 'ativo']),
            models.Index(fields=['tipo', 'ncm', 'ativo']),
            models.Index(fields=['tipo', 'uf', 'ativo']),
        ]
        verbose_name = 'Tabela Fiscal Auxiliar'
        verbose_name_plural = 'Tabelas Fiscais Auxiliares'

    def __str__(self):
        sufixo = f'/{self.uf}' if self.uf else ''
        return f'{self.get_tipo_display()} {self.codigo}{sufixo} - {self.descricao}'


class RegraFiscalUF(TimestampedModel):
    """UF-specific rule that complements product fiscal data suggestions."""

    uf = models.CharField(max_length=2, choices=UF.choices, db_index=True)
    ncm = models.CharField(max_length=8, blank=True, db_index=True)
    cest = models.CharField(max_length=7, blank=True, db_index=True)
    cfop = models.CharField(max_length=5, blank=True, db_index=True)
    regime_tributario = models.CharField(max_length=30, blank=True, db_index=True)
    aliquota_icms = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    aliquota_fcp = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    mva = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    reducao_base = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    fonte = models.CharField(max_length=120, blank=True)
    versao = models.CharField(max_length=40, blank=True)
    vigencia_inicio = models.DateField(null=True, blank=True)
    vigencia_fim = models.DateField(null=True, blank=True)
    observacao = models.TextField(blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'regras_fiscais_uf'
        ordering = ['uf', 'ncm', 'cfop']
        indexes = [
            models.Index(fields=['uf', 'ncm', 'ativo']),
            models.Index(fields=['uf', 'cest', 'ativo']),
            models.Index(fields=['uf', 'cfop', 'ativo']),
        ]
        verbose_name = 'Regra Fiscal por UF'
        verbose_name_plural = 'Regras Fiscais por UF'

    def __str__(self):
        alvo = self.ncm or self.cest or self.cfop or 'regra geral'
        return f'{self.uf} - {alvo}'


class AliquotaIBPT(TimestampedModel):
    """Carga tributaria aproximada por NCM, UF, versao e vigencia."""

    ncm = models.CharField(max_length=8, db_index=True)
    uf = models.CharField(max_length=2, choices=UF.choices, db_index=True)
    descricao = models.CharField(max_length=500, blank=True)
    federal_nacional = models.DecimalField(max_digits=7, decimal_places=4)
    federal_importado = models.DecimalField(max_digits=7, decimal_places=4)
    estadual = models.DecimalField(max_digits=7, decimal_places=4)
    municipal = models.DecimalField(max_digits=7, decimal_places=4)
    fonte = models.CharField(max_length=120)
    versao = models.CharField(max_length=20)
    vigencia_inicio = models.DateField(db_index=True)
    vigencia_fim = models.DateField(db_index=True)

    class Meta:
        db_table = 'aliquotas_ibpt'
        ordering = ['uf', 'ncm', '-vigencia_inicio']
        constraints = [
            models.UniqueConstraint(
                fields=['uf', 'ncm', 'versao'], name='uq_ibpt_uf_ncm_versao'
            ),
        ]
        indexes = [
            models.Index(
                fields=['uf', 'ncm', 'vigencia_inicio', 'vigencia_fim'],
                name='ibpt_uf_ncm_vigencia_idx',
            ),
        ]
        verbose_name = 'Aliquota IBPT'
        verbose_name_plural = 'Aliquotas IBPT'

    def __str__(self):
        return f'{self.ncm}/{self.uf} - {self.versao}'
