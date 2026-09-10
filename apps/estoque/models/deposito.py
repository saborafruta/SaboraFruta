"""
Depósito — local de estoque dentro de uma filial.

Toda filial tem pelo menos um depósito, marcado como `is_padrao=True`. Ele
serve para separar, no MESMO CNPJ e endereço, baldes de saldo com
finalidades diferentes — tipicamente "Loja" (mercadoria para revenda) e
"Fábrica" (matéria-prima e produção). Movimentar entre depósitos da mesma
filial é operação interna: não gera NF-e.

Fase 1 desta funcionalidade: o model existe e toda escrita de estoque passa
a carimbar o depósito, mas cada filial ainda tem só o depósito padrão, então
nada muda para o usuário. As telas para criar outros depósitos e as regras
de produção/venda por depósito vêm nas fases seguintes.
"""
from django.db import models

from apps.core.models.base import FilialScopedModel


class Deposito(FilialScopedModel):
    """Local de estoque de uma filial. Ver docstring do módulo."""

    class Tipo(models.TextChoices):
        GERAL = 'geral', 'Geral'
        REVENDA = 'revenda', 'Revenda'
        PRODUCAO = 'producao', 'Produção'

    nome = models.CharField(max_length=60)
    tipo = models.CharField(
        max_length=12, choices=Tipo.choices, default=Tipo.GERAL,
    )
    is_padrao = models.BooleanField(
        default=False,
        help_text='Depósito usado quando a operação não indica outro. Um por filial.',
    )
    permite_venda = models.BooleanField(
        default=True,
        help_text='Se desmarcado, o saldo deste depósito não fica disponível para venda.',
    )
    permite_producao = models.BooleanField(
        default=True,
        help_text='Se desmarcado, a produção não consome nem dá entrada neste depósito.',
    )
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'estoque_depositos'
        ordering = ['filial', 'nome']
        constraints = [
            models.UniqueConstraint(
                fields=['filial', 'nome'],
                name='deposito_nome_unico_por_filial',
            ),
            models.UniqueConstraint(
                fields=['filial'],
                condition=models.Q(is_padrao=True),
                name='deposito_um_padrao_por_filial',
            ),
        ]
        verbose_name = 'Depósito'
        verbose_name_plural = 'Depósitos'

    def __str__(self):
        return f'{self.nome} — {self.filial}'

    # ------------------------------------------------------------------
    # Resolução do depósito padrão
    # ------------------------------------------------------------------

    NOME_PADRAO = 'Estoque Geral'

    @classmethod
    def padrao_id(cls, filial_id: int) -> int:
        """
        PK do depósito padrão da filial. Cria sob demanda se ainda não
        existir — assim `MovimentacaoService` nunca quebra por uma filial
        criada fora do fluxo que semeia o depósito.
        """
        pk = (
            cls.objects.filter(filial_id=filial_id, is_padrao=True)
            .values_list('pk', flat=True)
            .first()
        )
        if pk:
            return pk
        deposito, _ = cls.objects.get_or_create(
            filial_id=filial_id,
            nome=cls.NOME_PADRAO,
            defaults={'is_padrao': True, 'tipo': cls.Tipo.GERAL},
        )
        if not deposito.is_padrao:
            deposito.is_padrao = True
            deposito.save(update_fields=['is_padrao', 'updated_at'])
        return deposito.pk
