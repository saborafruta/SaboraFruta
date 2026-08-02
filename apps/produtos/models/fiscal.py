"""
Classe Fiscal e Alíquotas com vigência (resolve P12 do banco).
Naturezas de Operação para vincular CFOP obrigatório.
"""
from django.db import models
from django.db.models import Q

from apps.core.constants.choices import UF
from apps.core.models.base import FilialManager, TimestampedModel


class _FilialVinculoManager(FilialManager):
    related_name = ''

    def for_filial(self, filial):
        if filial is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(
            **{f'{self.related_name}__filial': filial, f'{self.related_name}__ativo': True},
        ).distinct()

    def for_empresa(self, empresa):
        if empresa is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(
            **{f'{self.related_name}__filial__empresa': empresa, f'{self.related_name}__ativo': True},
        ).distinct()


class ClasseFiscalManager(_FilialVinculoManager):
    related_name = 'filiais_vinculo'


class NaturezaOperacaoManager(_FilialVinculoManager):
    related_name = 'filiais_vinculo'


class ClasseFiscal(TimestampedModel):
    """Grupo de tributação. Define CSTs padrão e agrupa alíquotas por UF."""

    empresa = models.ForeignKey(
        'core.Empresa', on_delete=models.CASCADE, related_name='classes_fiscais',
    )
    codigo = models.CharField(max_length=20)
    descricao = models.CharField(max_length=100)

    # CSTs padrão
    cst_icms_padrao = models.CharField(max_length=3, blank=True)
    csosn_padrao = models.CharField(max_length=3, blank=True, help_text='Para Simples Nacional')
    cst_pis_padrao = models.CharField(max_length=2, blank=True)
    cst_cofins_padrao = models.CharField(max_length=2, blank=True)
    cst_ipi_padrao = models.CharField(max_length=2, blank=True)

    pis_cofins_monofasico = models.BooleanField(default=False)
    ipi_suspenso = models.BooleanField(default=False, help_text='IPI suspenso em exportações')
    id_externo = models.CharField(max_length=100, blank=True, db_index=True)
    ativo = models.BooleanField(default=True, db_index=True)

    objects = ClasseFiscalManager()

    class Meta:
        db_table = 'classes_fiscais'
        ordering = ['codigo']
        unique_together = [('empresa', 'codigo')]
        verbose_name = 'Classe Fiscal'
        verbose_name_plural = 'Classes Fiscais'

    def __str__(self):
        return f'{self.codigo} — {self.descricao}'


class ClasseFiscalFilial(TimestampedModel):
    classe_fiscal = models.ForeignKey(ClasseFiscal, on_delete=models.CASCADE, related_name='filiais_vinculo')
    filial = models.ForeignKey('core.Filial', on_delete=models.CASCADE, related_name='classes_fiscais_vinculadas')
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'classes_fiscais_filiais'
        unique_together = [('classe_fiscal', 'filial')]
        indexes = [
            models.Index(fields=['filial', 'ativo']),
            models.Index(fields=['classe_fiscal', 'filial']),
        ]
        verbose_name = 'Classe Fiscal por Filial'
        verbose_name_plural = 'Classes Fiscais por Filial'

    def __str__(self):
        return f'{self.classe_fiscal} - {self.filial}'


class ClasseFiscalAliquotaQuerySet(models.QuerySet):
    def vigentes(self, data=None):
        """Filtra alíquotas vigentes na data informada (default: hoje)."""
        from django.utils import timezone
        data = data or timezone.localdate()
        return self.filter(
            Q(vigencia_inicio__lte=data)
            & (Q(vigencia_fim__gte=data) | Q(vigencia_fim__isnull=True))
        )


class ClasseFiscalAliquota(models.Model):
    """
    Alíquotas por classe fiscal × UF com histórico de vigência (resolve P12).
    Suporta Reforma Tributária (IBS/CBS/IS) com vigência gradual 2026-2033.
    """

    classe_fiscal = models.ForeignKey(
        ClasseFiscal, on_delete=models.CASCADE, related_name='aliquotas',
    )
    uf_destino = models.CharField(max_length=2, choices=UF.choices)

    # ICMS
    icms_interno = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    icms_interestadual = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    icms_importado = models.DecimalField(max_digits=5, decimal_places=2, default=4)
    reducao_base_icms = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    # Substituição Tributária
    tem_st = models.BooleanField(default=False)
    mva_original = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    mva_ajustado = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    pauta_fiscal = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    icms_st = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    # FCP (resolve P4)
    fcp = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    fcpst = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    fcp_retido = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    # DIFAL
    tem_difal = models.BooleanField(default=False)
    difal_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    # IPI, PIS, COFINS, ISS
    ipi = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    pis = models.DecimalField(max_digits=5, decimal_places=2, default=0.65)
    cofins = models.DecimalField(max_digits=5, decimal_places=2, default=3)
    iss = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    # Reforma Tributária (IBS/CBS/IS) — vigência gradual 2026-2033
    ibs = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text='IBS (substitui ICMS+ISS)')
    cbs = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text='CBS (substitui PIS+COFINS)')
    is_aliquota = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text='Imposto Seletivo')

    # Vigência (resolve P12)
    vigencia_inicio = models.DateField()
    vigencia_fim = models.DateField(null=True, blank=True, help_text='NULL = vigente até hoje')
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ClasseFiscalAliquotaQuerySet.as_manager()

    class Meta:
        db_table = 'classe_fiscal_aliquotas'
        ordering = ['classe_fiscal', 'uf_destino', '-vigencia_inicio']
        indexes = [
            models.Index(fields=['classe_fiscal', 'uf_destino', 'vigencia_inicio']),
        ]


class NaturezaOperacao(TimestampedModel):
    """Vincula CFOP obrigatório à operação (resolve P8)."""

    class Tipo(models.TextChoices):
        VENDA = 'venda', 'Venda'
        COMPRA = 'compra', 'Compra'
        DEVOLUCAO_CLIENTE = 'devolucao_cliente', 'Devolução de Cliente'
        DEVOLUCAO_FORNECEDOR = 'devolucao_fornecedor', 'Devolução ao Fornecedor'
        TRANSFERENCIA = 'transferencia', 'Transferência'
        EXPORTACAO = 'exportacao', 'Exportação'
        IMPORTACAO = 'importacao', 'Importação'
        BRINDE = 'brinde', 'Brinde'
        USO_PROPRIO = 'uso_proprio', 'Uso Próprio'
        BAIXA_VALIDADE = 'baixa_validade', 'Baixa por Validade'
        AJUSTE = 'ajuste', 'Ajuste'

    class TipoMovimentacao(models.TextChoices):
        ENTRADA = 'entrada', 'Entrada'
        SAIDA = 'saida', 'Saída'

    empresa = models.ForeignKey(
        'core.Empresa', on_delete=models.CASCADE, related_name='naturezas_operacao',
    )
    descricao = models.CharField(max_length=100)
    tipo = models.CharField(max_length=30, choices=Tipo.choices)
    cfop_dentro_estado = models.CharField(max_length=5)
    cfop_fora_estado = models.CharField(max_length=5, blank=True)
    cfop_exportacao = models.CharField(max_length=5, blank=True)

    gera_nfe = models.BooleanField(default=True)
    gera_nfce = models.BooleanField(default=False)
    movimenta_estoque = models.BooleanField(default=True)
    movimenta_financeiro = models.BooleanField(default=True)
    tipo_movimentacao_estoque = models.CharField(
        max_length=10, choices=TipoMovimentacao.choices, blank=True,
    )
    id_externo = models.CharField(max_length=100, blank=True, db_index=True)
    ativo = models.BooleanField(default=True, db_index=True)

    objects = NaturezaOperacaoManager()

    class Meta:
        db_table = 'naturezas_operacao'
        ordering = ['descricao']
        verbose_name = 'Natureza de Operação'
        verbose_name_plural = 'Naturezas de Operação'

    def __str__(self):
        return f'{self.descricao} ({self.cfop_dentro_estado})'


class NaturezaOperacaoFilial(TimestampedModel):
    natureza = models.ForeignKey(NaturezaOperacao, on_delete=models.CASCADE, related_name='filiais_vinculo')
    filial = models.ForeignKey('core.Filial', on_delete=models.CASCADE, related_name='naturezas_operacao_vinculadas')
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'naturezas_operacao_filiais'
        unique_together = [('natureza', 'filial')]
        indexes = [
            models.Index(fields=['filial', 'ativo']),
            models.Index(fields=['natureza', 'filial']),
        ]
        verbose_name = 'Natureza de Operacao por Filial'
        verbose_name_plural = 'Naturezas de Operacao por Filial'

    def __str__(self):
        return f'{self.natureza} - {self.filial}'
