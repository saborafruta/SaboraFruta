from django.db import models
from django.db.models import Q

from apps.core.models.base import FilialScopedModel


class Funcionario(FilialScopedModel):
    class TipoConta(models.TextChoices):
        CORRENTE = "corrente", "Conta corrente"
        POUPANCA = "poupanca", "Conta poupanca"
        PAGAMENTO = "pagamento", "Conta pagamento"

    nome = models.CharField(max_length=150)
    cpf = models.CharField(max_length=11, blank=True, db_index=True)
    cargo = models.CharField(max_length=100, blank=True)
    data_admissao = models.DateField(null=True, blank=True)
    salario_base = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    email = models.EmailField(max_length=120, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    chave_pix = models.CharField(max_length=150, blank=True)
    banco = models.CharField(max_length=100, blank=True)
    agencia = models.CharField(max_length=20, blank=True)
    conta = models.CharField(max_length=30, blank=True)
    tipo_conta = models.CharField(max_length=12, choices=TipoConta.choices, blank=True)
    observacao = models.TextField(blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "funcionarios"
        ordering = ["nome"]
        verbose_name = "Funcionario"
        verbose_name_plural = "Funcionarios"
        constraints = [
            models.UniqueConstraint(
                fields=["filial", "cpf"],
                condition=~Q(cpf=""),
                name="uniq_funcionario_filial_cpf",
            ),
        ]
        indexes = [
            models.Index(fields=["filial", "ativo"], name="func_filial_ativo_idx"),
            models.Index(fields=["filial", "nome"], name="func_filial_nome_idx"),
        ]

    def __str__(self):
        return self.nome
