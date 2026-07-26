"""Extrato/ledger da carteira de cashback — fonte da verdade de todo saldo."""
import uuid

from django.db import models

from apps.core.models.base import TimestampedModel


class MovimentoCashback(TimestampedModel):
    """
    Um lançamento imutável no extrato da carteira. Nunca é editado ou
    apagado — correções/estornos sempre criam um novo lançamento
    compensatório, preservando o histórico completo para auditoria.
    """

    class Tipo(models.TextChoices):
        CREDITO_VENDA = "credito_venda", "Crédito por venda"
        DEBITO_UTILIZACAO = "debito_utilizacao", "Débito por utilização"
        ESTORNO = "estorno", "Estorno"
        CANCELAMENTO = "cancelamento", "Cancelamento"
        EXPIRACAO = "expiracao", "Expiração"
        AJUSTE_MANUAL = "ajuste_manual", "Ajuste manual"

    class Origem(models.TextChoices):
        PDV = "pdv", "PDV"
        MANUAL = "manual", "Manual"
        SISTEMA = "sistema", "Sistema"
        API = "api", "API"

    TIPOS_CREDITO = (Tipo.CREDITO_VENDA, Tipo.ESTORNO, Tipo.AJUSTE_MANUAL)
    TIPOS_DEBITO = (Tipo.DEBITO_UTILIZACAO, Tipo.CANCELAMENTO, Tipo.EXPIRACAO)

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)

    carteira = models.ForeignKey(
        "cashback.CarteiraCashback", on_delete=models.PROTECT, related_name="movimentos",
    )
    cliente = models.ForeignKey(
        "cadastros.Cliente", on_delete=models.PROTECT, related_name="movimentos_cashback",
    )
    empresa = models.ForeignKey(
        "core.Empresa", on_delete=models.PROTECT, related_name="movimentos_cashback",
    )
    filial = models.ForeignKey(
        "core.Filial", on_delete=models.SET_NULL, null=True, blank=True, related_name="movimentos_cashback",
    )
    venda = models.ForeignKey(
        "pdv.VendaPDV", on_delete=models.SET_NULL, null=True, blank=True, related_name="movimentos_cashback",
    )
    item_venda = models.ForeignKey(
        "pdv.ItemVendaPDV", on_delete=models.SET_NULL, null=True, blank=True, related_name="movimentos_cashback",
    )
    usuario = models.ForeignKey(
        "core.Usuario", on_delete=models.SET_NULL, null=True, blank=True, related_name="movimentos_cashback",
        help_text="Operador responsável pelo lançamento (venda, ajuste manual, etc).",
    )

    tipo = models.CharField(max_length=20, choices=Tipo.choices, db_index=True)
    valor = models.DecimalField(max_digits=14, decimal_places=2, help_text="Sempre positivo; a direção é definida pelo tipo.")
    saldo_apos = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    data_validade = models.DateField(
        null=True, blank=True,
        help_text="Só preenchido em créditos por venda — data em que este crédito expira.",
    )

    observacao = models.TextField(blank=True)
    origem = models.CharField(max_length=20, choices=Origem.choices, default=Origem.SISTEMA)
    ip_origem = models.GenericIPAddressField(null=True, blank=True)
    dispositivo = models.CharField(max_length=255, blank=True)

    chave_idempotencia = models.CharField(
        max_length=100, unique=True, null=True, blank=True,
        help_text="Chave única para impedir duplicidade (ex.: 'venda:123:credito').",
    )

    class Meta:
        db_table = "cashback_movimento"
        verbose_name = "Movimento de Cashback"
        verbose_name_plural = "Movimentos de Cashback"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["carteira", "-created_at"]),
            models.Index(fields=["cliente", "-created_at"]),
            models.Index(fields=["tipo", "data_validade"]),
            models.Index(fields=["venda"]),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} — R$ {self.valor} ({self.cliente})"

    @property
    def eh_credito(self) -> bool:
        return self.tipo in self.TIPOS_CREDITO

    @property
    def eh_debito(self) -> bool:
        return self.tipo in self.TIPOS_DEBITO
