"""
Grade do pedido: quantidade por tamanho, item a item.

É a tabela da ficha — CONJUNTO com 1 em G e 1 em GG, total 2.

Regra que define o desenho: "nunca permitir divergência entre a grade e a
quantidade total". Validar não bastaria — o usuário salvaria a divergência e
só depois receberia o aviso. Por isso `ItemPedidoProducao.quantidade` passa
a ser DERIVADA desta tabela: quem tem grade não digita o total, o total é a
soma. Ver `services/grade_pedido.py`.

Linhas com quantidade zero são mantidas de propósito: elas é que definem
quais colunas (tamanhos) a tabela mostra. Apagá-las faria a coluna sumir
assim que alguém zerasse o valor para corrigir.
"""
from django.db import models


class ItemGradePedido(models.Model):
    """Quantidade de um tamanho, num item do pedido."""

    item = models.ForeignKey(
        'moda.ItemPedidoProducao', on_delete=models.CASCADE, related_name='grade',
    )
    tamanho = models.ForeignKey(
        'moda.Tamanho', on_delete=models.PROTECT, related_name='grades_pedido',
    )
    quantidade = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'moda_grade_pedido'
        # A ordem do tamanho manda: a grade é lida PP, P, M, G, GG, XGG.
        ordering = ['tamanho__ordem', 'tamanho__sigla']
        unique_together = [('item', 'tamanho')]
        verbose_name = 'Tamanho do item'
        verbose_name_plural = 'Grade do item'

    def __str__(self):
        return f'{self.tamanho.sigla}: {self.quantidade}'
