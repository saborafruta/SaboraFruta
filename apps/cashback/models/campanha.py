"""Campanhas promocionais de cashback (ex.: Black Friday, Dobro de Cashback)."""
from django.db import models

from apps.core.models.base import ActiveModel, TimestampedModel


class CampanhaCashback(TimestampedModel, ActiveModel):
    empresa = models.ForeignKey(
        "core.Empresa", on_delete=models.CASCADE, related_name="campanhas_cashback",
    )
    nome = models.CharField(max_length=100)
    descricao = models.TextField(blank=True)
    percentual = models.DecimalField(max_digits=5, decimal_places=2)
    data_inicio = models.DateField()
    data_fim = models.DateField()
    prioridade = models.PositiveIntegerField(
        default=0,
        help_text="Quando mais de uma campanha se aplica no mesmo dia, vence a de maior prioridade.",
    )
    dias_semana = models.JSONField(
        default=list, blank=True,
        help_text="Lista de dias da semana em que a campanha vale (0=segunda … 6=domingo). Vazio = todos os dias.",
    )
    produtos = models.ManyToManyField(
        "produtos.Produto", blank=True, related_name="campanhas_cashback",
        help_text="Restringe a campanha a produtos específicos. Vazio = todos os produtos elegíveis.",
    )
    categorias = models.ManyToManyField(
        "produtos.CategoriaProduto", blank=True, related_name="campanhas_cashback",
        help_text="Restringe a campanha a categorias específicas. Vazio = todas as categorias.",
    )
    filiais = models.ManyToManyField(
        "core.Filial", blank=True, related_name="campanhas_cashback",
        help_text="Restringe a campanha a filiais específicas. Vazio = todas as filiais da empresa.",
    )

    class Meta:
        db_table = "cashback_campanha"
        verbose_name = "Campanha de Cashback"
        verbose_name_plural = "Campanhas de Cashback"
        ordering = ["-prioridade", "-data_inicio"]
        indexes = [
            models.Index(fields=["empresa", "ativo", "data_inicio", "data_fim"]),
        ]

    def __str__(self):
        return f"{self.nome} ({self.percentual}%)"

    def vigente_em(self, data) -> bool:
        if not self.ativo:
            return False
        if not (self.data_inicio <= data <= self.data_fim):
            return False
        if self.dias_semana and data.weekday() not in self.dias_semana:
            return False
        return True

    def aplica_a(self, *, produto=None, categoria=None, filial=None) -> bool:
        if self.filiais.exists() and filial is not None and not self.filiais.filter(pk=filial.pk).exists():
            return False
        restringe_produto = self.produtos.exists()
        restringe_categoria = self.categorias.exists()
        if not restringe_produto and not restringe_categoria:
            return True
        if restringe_produto and produto is not None and self.produtos.filter(pk=produto.pk).exists():
            return True
        if restringe_categoria and categoria is not None and self.categorias.filter(pk=categoria.pk).exists():
            return True
        return False
