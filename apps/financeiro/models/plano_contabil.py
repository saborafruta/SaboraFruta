from django.db import models

from apps.core.models import Empresa
from apps.core.models.base import ActiveModel, TimestampedModel


class PlanoContabil(TimestampedModel, ActiveModel):
    class TipoConta(models.TextChoices):
        SINTETICA = "S", "Sintética"
        ANALITICA = "A", "Analítica"

    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="plano_contabil",
    )
    conta_pai = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="contas_filhas",
    )
    codigo_referencia = models.PositiveIntegerField()
    classificacao = models.CharField(max_length=20)
    tipo_conta = models.CharField(max_length=1, choices=TipoConta.choices)
    descricao = models.CharField(max_length=255)
    codigo_dre = models.CharField(max_length=20, blank=True)
    data_inicio = models.DateField()
    nivel = models.PositiveSmallIntegerField()
    ordem = models.PositiveIntegerField()
    pagina_origem = models.PositiveSmallIntegerField(null=True, blank=True)
    origem = models.CharField(max_length=120, default="Relação de Contas - Contabilidade")

    class Meta:
        db_table = "plano_contabil"
        ordering = ["ordem"]
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "classificacao"],
                name="uniq_plano_contabil_empresa_classificacao",
            ),
            models.UniqueConstraint(
                fields=["empresa", "codigo_referencia"],
                name="uniq_plano_contabil_empresa_codigo_ref",
            ),
        ]
        indexes = [
            models.Index(fields=["empresa", "ordem"], name="pl_cont_emp_ord_idx"),
            models.Index(fields=["empresa", "tipo_conta"], name="pl_cont_emp_tipo_idx"),
            models.Index(fields=["empresa", "ativo"], name="pl_cont_emp_ativo_idx"),
        ]
        verbose_name = "Conta contábil"
        verbose_name_plural = "Plano contábil"

    @property
    def aceita_lancamento(self):
        return self.tipo_conta == self.TipoConta.ANALITICA

    @property
    def recuo_px(self):
        return max(self.nivel - 1, 0) * 22

    def __str__(self):
        return f"{self.classificacao} - {self.descricao}"
