"""Configuracao de cashback — parametros globais e por filial."""
from django.db import models

from apps.core.models.base import TimestampedModel


class ConfiguracaoCashback(TimestampedModel):
    """
    Parametros de cashback de uma filial. Se a filial nao tiver
    configuracao propria (ou estiver inativa), o resolver cai para a
    configuracao da empresa (filial=NULL) como fallback global.
    """

    class ModoEstornoUsado(models.TextChoices):
        NEGATIVO = "negativo", "Permitir saldo negativo na carteira"
        CONTA_A_RECEBER = "conta_a_receber", "Gerar conta a receber do cliente"

    empresa = models.ForeignKey(
        "core.Empresa", on_delete=models.CASCADE, related_name="configuracoes_cashback",
    )
    filial = models.ForeignKey(
        "core.Filial", on_delete=models.CASCADE, null=True, blank=True,
        related_name="configuracoes_cashback",
        help_text="Deixe em branco para configuração padrão da empresa (fallback global).",
    )

    percentual_global = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        help_text="Percentual de cashback aplicado quando nenhuma regra mais específica existir.",
    )
    valor_minimo_gerar = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="Valor mínimo da compra para gerar cashback.",
    )
    valor_minimo_usar = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="Valor mínimo da compra para permitir uso de cashback como pagamento.",
    )
    percentual_maximo_uso_venda = models.DecimalField(
        max_digits=5, decimal_places=2, default=100,
        help_text="Percentual máximo do total da venda que pode ser pago com cashback.",
    )
    dias_validade = models.PositiveIntegerField(
        default=90,
        help_text="Dias até o crédito de cashback expirar.",
    )
    modo_estorno_usado = models.CharField(
        max_length=20, choices=ModoEstornoUsado.choices, default=ModoEstornoUsado.CONTA_A_RECEBER,
        help_text="O que fazer quando uma venda cancelada já teve seu cashback gasto pelo cliente.",
    )
    ativo = models.BooleanField(default=True)

    class Meta:
        db_table = "cashback_configuracao"
        verbose_name = "Configuração de Cashback"
        verbose_name_plural = "Configurações de Cashback"
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "filial"], name="uniq_configuracao_cashback_empresa_filial",
            ),
        ]

    def __str__(self):
        alvo = self.filial.nome_fantasia if self.filial_id else f"{self.empresa} (padrão)"
        return f"Cashback — {alvo}"
