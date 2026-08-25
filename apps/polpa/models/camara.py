"""
A câmara fria e o endereço de cada lote dentro dela.

POR QUE ISTO NÃO É "ESTOQUE". O ERP já sabe QUANTO existe de cada produto e
de cada lote — `Estoque` e `LoteProduto` respondem isso, e duplicá-los aqui
daria dois saldos da mesma coisa. O que ele não sabe é ONDE, e numa fábrica
de congelados essa é a pergunta cara:

  · quem separa um pedido às 4h da manhã precisa saber em qual câmara e em
    qual rua está o lote. Sem endereço, procura-se — e cada minuto de porta
    de câmara aberta é temperatura subindo em tudo que está lá dentro;

  · quando a câmara dá defeito, a pergunta é "o que estava nela". Sem o
    vínculo, a resposta é uma conferência física de madrugada;

  · a fiscalização pergunta a que temperatura o lote foi guardado. A câmara
    tem a faixa; o lote precisa apontar para ela.

O PESO NÃO É GUARDADO, É CALCULADO. Peso do lote é quantidade × peso do
produto, e as duas parcelas já existem no cadastro. Gravar um terceiro
número daria a chance de ele discordar dos outros dois — e o dia em que
discordar é o dia em que ninguém sabe qual está certo.
"""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel

ZERO = Decimal('0')


class Camara(FilialScopedModel):
    """Uma câmara fria, com a faixa de temperatura que ela mantém."""

    class Tipo(models.TextChoices):
        CONGELADOS = 'congelados', 'Congelados'
        RESFRIADOS = 'resfriados', 'Resfriados'
        ANTECAMARA = 'antecamara', 'Antecâmara / expedição'
        TUNEL = 'tunel', 'Túnel de congelamento'

    nome = models.CharField(max_length=60, db_index=True)
    tipo = models.CharField(
        max_length=15, choices=Tipo.choices, default=Tipo.CONGELADOS,
    )

    # A FAIXA É DA CÂMARA, e o produto tem a dele (seção 1). É o cruzamento
    # das duas que diz se aquele lote pode ficar ali: guardar polpa a -18°C
    # numa câmara que só chega a -5°C é perder o lote devagar.
    temperatura_min = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text='°C que a câmara mantém, no mínimo.',
    )
    temperatura_max = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text='°C que a câmara mantém, no máximo.',
    )

    capacidade_kg = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text='Quanto cabe. Nulo é "ninguém mediu", não "não cabe nada".',
    )

    ativo = models.BooleanField(default=True)
    observacao = models.TextField(blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_camaras'
        ordering = ['nome']
        unique_together = [('filial', 'nome')]
        indexes = [models.Index(fields=['filial', 'ativo'])]
        verbose_name = 'Câmara fria'
        verbose_name_plural = 'Câmaras frias'

    def __str__(self):
        return self.nome

    @property
    def faixa(self) -> str:
        """A faixa em texto — vazia quando ninguém definiu."""
        if self.temperatura_min is None and self.temperatura_max is None:
            return ''
        if self.temperatura_min is not None and self.temperatura_max is not None:
            return f'{self.temperatura_min}°C a {self.temperatura_max}°C'
        valor = (
            self.temperatura_min if self.temperatura_min is not None
            else self.temperatura_max
        )
        return f'{valor}°C'

    def cabe(self, produto) -> bool | None:
        """
        Se um produto pode ser guardado nesta câmara, pela temperatura.

        `None` quando falta a faixa de um dos dois — e a tela diz isso em vez
        de aprovar em silêncio: sem as duas faixas, não há como afirmar nada,
        e "não sei" é uma resposta melhor do que um "pode" inventado.
        """
        exigida = getattr(produto, 'temperatura_maxima', None)
        if exigida is None or self.temperatura_max is None:
            return None
        return self.temperatura_max <= exigida


class LoteArmazenado(FilialScopedModel):
    """Onde um lote está guardado — a câmara e o endereço dentro dela."""

    # UM PARA UM COM O LOTE DO ERP. O saldo continua sendo dele; aqui mora
    # só a localização. Um segundo saldo daria duas respostas para "quanto
    # tem", e a divergência apareceria justamente na conferência.
    lote = models.OneToOneField(
        'estoque.LoteProduto', on_delete=models.CASCADE,
        related_name='armazenamento_polpa',
    )
    camara = models.ForeignKey(
        Camara, on_delete=models.PROTECT, related_name='lotes',
    )
    endereco = models.CharField(
        max_length=40, blank=True,
        help_text='Rua, bloco, prateleira — como a câmara é organizada.',
    )

    # A TEMPERATURA DA ENTRADA, medida na hora de guardar. Não é a da
    # câmara: é a do PRODUTO quando entrou, e é ela que diz se o
    # congelamento terminou antes do armazenamento.
    temperatura_entrada = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
    )
    armazenado_em = models.DateTimeField(auto_now_add=True)
    observacao = models.TextField(blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_lotes_armazenados'
        ordering = ['camara__nome', 'endereco']
        indexes = [
            models.Index(fields=['filial', 'camara']),
        ]
        verbose_name = 'Lote armazenado'
        verbose_name_plural = 'Lotes armazenados'

    def __str__(self):
        return f'{self.lote.numero_lote} @ {self.camara}'

    # ── Leituras ─────────────────────────────────────────────────────────

    @property
    def peso(self) -> Decimal | None:
        """
        O peso do que está guardado: saldo × peso do produto.

        CALCULADO, não gravado: as duas parcelas já existem, e um terceiro
        número poderia discordar delas. `None` sem peso no cadastro do
        produto — zero seria um lote que não pesa nada.
        """
        unitario = getattr(self.lote.produto, 'peso_liquido', None)
        if not unitario:
            return None
        return ((self.lote.quantidade_atual or ZERO) * unitario).quantize(
            Decimal('0.001')
        )

    @property
    def dias_para_vencer(self) -> int | None:
        from django.utils import timezone

        validade = self.lote.data_validade
        if not validade:
            return None
        return (validade - timezone.localdate()).days

    @property
    def vencido(self) -> bool:
        dias = self.dias_para_vencer
        return dias is not None and dias < 0

    @property
    def fora_da_faixa(self) -> bool:
        """
        Lote guardado em câmara que não alcança a temperatura que ele exige.

        É a pergunta que ninguém faz até o produto chegar mole no cliente —
        e a resposta já existe nos dois cadastros, só faltava cruzá-los.
        """
        return self.camara.cabe(self.lote.produto) is False
