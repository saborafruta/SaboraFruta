"""
Painel visual do item: como a peça fica de frente e de costas.

São quatro vistas — frente e costas da camisa, frente e costas do calção —
porque a ficha do Grupo Eureka mostra exatamente isso: o conjunto desenhado
nas quatro posições, com o número "25" nas costas.

Cada vista aceita duas origens de imagem:
  - upload próprio, quando o designer mandou a arte daquele pedido;
  - mockup pré-cadastrado, quando é a mesma base de sempre.

Ter as duas evita o extremo ruim de cada lado: só upload obrigaria a
reenviar a mesma camisa branca em todo pedido; só catálogo impediria a arte
específica do cliente.
"""
from django.db import models

from apps.core.models.base import ActiveModel, FilialManager, FilialScopedModel


class Posicao(models.TextChoices):
    """As quatro vistas da ficha, na ordem em que são desenhadas."""

    FRENTE_CAMISA = 'frente_camisa', 'Frente da camisa'
    COSTAS_CAMISA = 'costas_camisa', 'Costas da camisa'
    FRENTE_CALCAO = 'frente_calcao', 'Frente do calção'
    COSTAS_CALCAO = 'costas_calcao', 'Costas do calção'


# Só nas costas se aplica nome e número — é ali que vão, na peça real.
POSICOES_COSTAS = (Posicao.COSTAS_CAMISA, Posicao.COSTAS_CALCAO)


class MockupVisual(FilialScopedModel, ActiveModel):
    """Imagem base reutilizável entre pedidos (a camisa lisa, o calção liso)."""

    nome = models.CharField(max_length=80)
    posicao = models.CharField(max_length=20, choices=Posicao.choices)
    imagem = models.ImageField(upload_to='moda/mockups/')

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_mockups'
        ordering = ['posicao', 'nome']
        unique_together = [('filial', 'nome', 'posicao')]
        verbose_name = 'Mockup visual'
        verbose_name_plural = 'Mockups visuais'

    def __str__(self):
        return f'{self.nome} — {self.get_posicao_display()}'


class VisualItemPedido(models.Model):
    """Uma das quatro vistas de um item do pedido."""

    item = models.ForeignKey(
        'moda.ItemPedidoProducao', on_delete=models.CASCADE, related_name='visuais',
    )
    posicao = models.CharField(max_length=20, choices=Posicao.choices)

    imagem = models.ImageField(
        upload_to='moda/visuais/', blank=True, null=True,
        help_text='Arte específica deste pedido.',
    )
    mockup = models.ForeignKey(
        MockupVisual, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='usos',
        help_text='Ou escolha uma imagem já cadastrada.',
    )

    # Nome e número desta vista. Só fazem sentido nas costas, e a view
    # valida isso. Ficam aqui, e não só na Personalização, porque são o que
    # está DESENHADO nesta vista -- a Personalização registra a técnica com
    # que serão aplicados. São perguntas diferentes sobre o mesmo valor.
    nome = models.CharField(max_length=80, blank=True)
    numero = models.CharField(max_length=10, blank=True)

    observacoes = models.CharField(max_length=160, blank=True)

    class Meta:
        db_table = 'moda_visuais_item'
        ordering = ['posicao']
        # Uma vista por posição por item: duas "frentes da camisa" no mesmo
        # item seriam contraditórias na hora de produzir.
        unique_together = [('item', 'posicao')]
        verbose_name = 'Visual do item'
        verbose_name_plural = 'Visuais do item'

    def __str__(self):
        return f'{self.item.nome_exibicao} — {self.get_posicao_display()}'

    @property
    def url_imagem(self) -> str:
        """A imagem própria manda; sem ela, a do mockup; sem as duas, vazio."""
        if self.imagem:
            return self.imagem.url
        if self.mockup_id and self.mockup.imagem:
            return self.mockup.imagem.url
        return ''

    @property
    def e_costas(self) -> bool:
        return self.posicao in POSICOES_COSTAS

    @property
    def tem_imagem(self) -> bool:
        return bool(self.url_imagem)
