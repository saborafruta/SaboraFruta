"""
Regras de percentual de cashback por nível de escopo.

Tabelas separadas (em vez de uma única tabela genérica com várias FKs
nulas) para manter unicidade e queries simples e diretas por nível.
A ordem de prioridade de resolução (produto > categoria > campanha >
filial > empresa > global) fica no resolver (services/regra_resolver.py),
não no modelo.
"""
from django.db import models

from apps.core.models.base import ActiveModel, TimestampedModel


class RegraCashbackProduto(TimestampedModel, ActiveModel):
    produto = models.OneToOneField(
        "produtos.Produto", on_delete=models.CASCADE, related_name="regra_cashback",
    )
    percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_minimo_gerar = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="Substitui o mínimo padrão da configuração, se informado.",
    )
    gera_cashback = models.BooleanField(
        default=True,
        help_text="Desmarque para excluir este produto do cashback, mesmo com percentual configurado.",
    )

    class Meta:
        db_table = "cashback_regra_produto"
        verbose_name = "Regra de Cashback por Produto"
        verbose_name_plural = "Regras de Cashback por Produto"

    def __str__(self):
        return f"Cashback {self.percentual}% — {self.produto}"


class RegraCashbackCategoria(TimestampedModel, ActiveModel):
    categoria = models.OneToOneField(
        "produtos.CategoriaProduto", on_delete=models.CASCADE, related_name="regra_cashback",
    )
    percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_minimo_gerar = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
    )
    gera_cashback = models.BooleanField(
        default=True,
        help_text="Desmarque para excluir toda a categoria do cashback.",
    )

    class Meta:
        db_table = "cashback_regra_categoria"
        verbose_name = "Regra de Cashback por Categoria"
        verbose_name_plural = "Regras de Cashback por Categoria"

    def __str__(self):
        return f"Cashback {self.percentual}% — {self.categoria}"


class RegraCashbackFilial(TimestampedModel, ActiveModel):
    filial = models.OneToOneField(
        "core.Filial", on_delete=models.CASCADE, related_name="regra_cashback",
    )
    percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_minimo_gerar = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
    )

    class Meta:
        db_table = "cashback_regra_filial"
        verbose_name = "Regra de Cashback por Filial"
        verbose_name_plural = "Regras de Cashback por Filial"

    def __str__(self):
        return f"Cashback {self.percentual}% — {self.filial}"


class RegraCashbackEmpresa(TimestampedModel, ActiveModel):
    empresa = models.OneToOneField(
        "core.Empresa", on_delete=models.CASCADE, related_name="regra_cashback",
    )
    percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_minimo_gerar = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
    )

    class Meta:
        db_table = "cashback_regra_empresa"
        verbose_name = "Regra de Cashback por Empresa"
        verbose_name_plural = "Regras de Cashback por Empresa"

    def __str__(self):
        return f"Cashback {self.percentual}% — {self.empresa}"
