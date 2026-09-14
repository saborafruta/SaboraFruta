"""Apresentacoes de venda/compra de um produto (multiplas unidades por produto).

Um Produto tem uma unica unidade base (`Produto.unidade_medida`). Cada
ProdutoApresentacao descreve uma forma adicional de comprar/vender esse
mesmo produto (ex: Unidade, Pacote 100, Pacote 500, Caixa 1.000) sem
duplicar o cadastro do produto.

DECISAO DE MODELAGEM (nao reintroduzir conversao encadeada no schema):
`fator_conversao` e SEMPRE absoluto e direto para a unidade base do
produto — nunca uma referencia a outra apresentacao. Ex: se "1 CX = 12
PCT" e "1 PCT = 10 UN", o fator gravado para a apresentacao CX e 120
(direto para UN), nao uma cadeia CX->PCT->UN.

Motivo: conversao encadeada entre apresentacoes cria arredondamento
cumulativo quando um elo muda de fator, e quebra a cadeia inteira se uma
apresentacao intermediaria for inativada. A entrada encadeada ("1 CX = 12
PCT") e permitida na UI/service como conveniencia de digitacao — quem
calcula e grava o fator absoluto resultante e
`apps.produtos.services.apresentacao_service`, nunca o model.
"""
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models.base import TimestampedModel


class ProdutoApresentacaoManager(models.Manager):
    def ativas(self):
        return self.get_queryset().filter(ativo=True)


class ProdutoApresentacao(TimestampedModel):
    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.CASCADE, related_name='apresentacoes',
    )
    unidade = models.ForeignKey(
        'produtos.UnidadeMedida', on_delete=models.PROTECT, related_name='apresentacoes_produto',
    )
    descricao = models.CharField(
        max_length=60, help_text='Ex: Unidade, Pacote 100, Caixa 1.000',
    )
    codigo = models.CharField(max_length=30, blank=True, help_text='Codigo interno da apresentacao')
    fator_conversao = models.DecimalField(
        max_digits=14, decimal_places=6, default=1,
        help_text='Quantidade de unidades base contida nesta apresentacao. Sempre absoluto, nunca encadeado.',
    )
    preco_venda = models.DecimalField(
        max_digits=14, decimal_places=4, default=0,
        help_text='Preco de venda proprio desta apresentacao (independente do preco_venda do produto).',
    )
    padrao = models.BooleanField(
        default=False,
        help_text='Apresentacao usada por padrao na venda/PDV quando nenhuma outra for informada.',
    )
    ativo = models.BooleanField(default=True, db_index=True)

    objects = ProdutoApresentacaoManager()

    class Meta:
        db_table = 'produtos_apresentacoes'
        ordering = ['produto', 'fator_conversao']
        unique_together = [('produto', 'unidade', 'descricao')]
        constraints = [
            models.UniqueConstraint(
                fields=['produto'],
                condition=Q(padrao=True),
                name='uniq_produto_apresentacao_padrao',
            ),
        ]
        indexes = [
            models.Index(fields=['produto', 'ativo']),
        ]
        verbose_name = 'Apresentacao de Produto'
        verbose_name_plural = 'Apresentacoes de Produto'

    def __str__(self):
        return f'{self.produto} - {self.descricao} ({self.fator_conversao}x)'

    def clean(self):
        super().clean()
        if self.fator_conversao is not None and self.fator_conversao <= 0:
            raise ValidationError({'fator_conversao': 'O fator de conversao deve ser maior que zero.'})

    def converter_para_base(self, quantidade):
        """Converte uma quantidade nesta apresentacao para a unidade base do produto."""
        return quantidade * self.fator_conversao

    def converter_de_base(self, quantidade_base):
        """Converte uma quantidade na unidade base do produto para esta apresentacao."""
        return quantidade_base / self.fator_conversao
