"""
Personalização por pessoa: a lista de jogadores do pedido.

Cada linha é uma peça nominal — "SILVA, 10, G, Camisa". É o detalhamento
da grade: onde a grade diz "3 peças no G", esta lista diz quais três.

A conferência com a grade é feita em `services/individual.py` e é
CONFERÊNCIA, não trava. Durante a digitação a lista fica naturalmente
incompleta, e bloquear ali impediria salvar o trabalho pela metade. A tela
mostra a diferença o tempo todo, para ninguém fechar o pedido sem ver.
"""
from django.db import models


class PersonalizacaoIndividual(models.Model):
    """Uma pessoa e a peça dela dentro do pedido."""

    pedido = models.ForeignKey(
        'moda.PedidoProducao', on_delete=models.CASCADE, related_name='individuais',
    )
    # O item diz qual produto é a peça desta pessoa. Obrigatório: sem ele,
    # não dá para conferir contra a grade, que é por item.
    item = models.ForeignKey(
        'moda.ItemPedidoProducao', on_delete=models.CASCADE, related_name='individuais',
    )
    tamanho = models.ForeignKey(
        'moda.Tamanho', on_delete=models.PROTECT, related_name='individuais',
    )

    tamanho_calcao = models.ForeignKey(
        'moda.Tamanho', on_delete=models.PROTECT, null=True, blank=True,
        related_name='individuais_calcao', verbose_name='Tamanho do calção',
    )

    nome = models.CharField(max_length=80, blank=True)
    numero = models.CharField(max_length=10, blank=True)
    nome_calcao = models.CharField(max_length=80, blank=True)
    numero_calcao = models.CharField(max_length=10, blank=True)
    observacoes = models.CharField(max_length=160, blank=True)

    ordem = models.PositiveIntegerField(default=0)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'moda_personalizacao_individual'
        # A lista acompanha a grade em toda saída: OP, orçamento, PDFs e
        # conferência. A ordem de lançamento não pode produzir XGG antes de
        # PP quando a peça chega à produção.
        ordering = ['tamanho__ordem', 'tamanho__sigla', 'ordem', 'id']
        indexes = [models.Index(fields=['pedido', 'item', 'tamanho'])]
        verbose_name = 'Personalização individual'
        verbose_name_plural = 'Personalizações individuais'

    def __str__(self):
        partes = [p for p in (self.numero, self.nome) if p]
        return ' — '.join(partes) if partes else f'Sem nome ({self.tamanho})'

    @property
    def identificacao(self) -> str:
        """Como a peça é reconhecida na produção."""
        if self.nome and self.numero:
            return f'{self.nome} #{self.numero}'
        return self.nome or (f'#{self.numero}' if self.numero else 'Sem identificação')
