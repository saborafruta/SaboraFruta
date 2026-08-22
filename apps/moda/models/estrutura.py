"""
Estrutura do produto — a composição em níveis.

O "Conjunto — Camisa + Calção" que aparece no pedido é um produto que vale
por outros dois. Sem esta tabela ele só existia como TEXTO na descrição do
item: ninguém sabia que produzir um conjunto é produzir uma camisa e um
calção, e o custo do conjunto tinha de ser digitado à mão em vez de sair da
soma das partes.

O QUE ESTA TABELA NÃO É: a lista de materiais. Essa é da ficha técnica
(`MaterialFicha`), e continua sendo — tecido, linha e botão entram na peça,
não são peças. Aqui é produto dentro de produto, e cada componente traz a
própria ficha junto quando a estrutura é explodida.

CICLO é o risco real: nada no banco impede gravar A dentro de B e B dentro
de A, e a partir daí qualquer leitura da árvore roda para sempre. Quem
barra é o `EstruturaService`, na gravação, e a leitura ainda assim corta
repetição — defesa em dois lugares porque uma linha criada por importação
ou por shell não passa pela view.
"""
from decimal import Decimal

from django.db import models


class EstruturaProduto(models.Model):
    """Um componente dentro de outro produto, com a quantidade que entra."""

    pai = models.ForeignKey(
        'moda.ProdutoModa', on_delete=models.CASCADE, related_name='componentes',
        verbose_name='Produto',
    )
    # PROTECT no componente: apagar do catálogo uma peça que compõe outra
    # deixaria o conjunto sem uma das partes, e em silêncio. O CASCADE fica
    # só no pai, onde apagar o conjunto realmente dissolve a composição.
    componente = models.ForeignKey(
        'moda.ProdutoModa', on_delete=models.PROTECT, related_name='usado_em',
    )
    quantidade = models.DecimalField(
        max_digits=10, decimal_places=3, default=Decimal('1'),
        help_text='Quantas unidades do componente entram em UMA unidade do produto.',
    )
    ordem = models.PositiveIntegerField(
        default=0, help_text='Ordem de leitura na estrutura.',
    )
    observacao = models.CharField(max_length=160, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'moda_estrutura_produto'
        ordering = ['ordem', 'id']
        # O mesmo componente duas vezes no mesmo pai seria uma linha
        # duplicada, não dois itens: quem precisa de dois calções aumenta a
        # QUANTIDADE, e assim o número fica num lugar só.
        unique_together = [('pai', 'componente')]
        verbose_name = 'Componente da estrutura'
        verbose_name_plural = 'Estrutura do produto'

    def __str__(self):
        return f'{self.pai} ← {self.quantidade:g} × {self.componente}'
