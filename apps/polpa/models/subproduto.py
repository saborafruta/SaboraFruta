"""
O que sai da batida além do produto — casca, caroço, bagaço, fruta refugada.

SUBPRODUTO NÃO É PERDA, e é essa a razão desta tabela existir separada.
`ApontamentoEtapa.perda` é peso que entrou menos peso que saiu: um número, sem
nome e sem destino. Ele responde "quanto sumiu" e não responde as duas
perguntas que decidem dinheiro:

  · O QUE ERA. Casca de manga e fruta refugada saem os dois do despolpamento e
    não valem a mesma coisa: uma vai para ração, a outra pode voltar para
    polpa de segunda;

  · PARA ONDE FOI. Bagaço vendido para o pecuarista é RECEITA. Bagaço no
    caminhão da prefeitura é CUSTO -- destinação de resíduo orgânico se paga.
    Os dois pesam igual na balança e são o oposto um do outro no resultado,
    e um relatório de "perdas" que junta os dois esconde exatamente isso.

NÃO SOMA À PERDA, EXPLICA A PERDA. O subproduto declara de qual etapa ele
saiu; o peso dele já está dentro daquela perda. Somá-los contaria a mesma
casca duas vezes -- e o rendimento da batida despencaria no papel sem que nada
tivesse mudado no chão.

REAPROVEITAMENTO E USO INTERNO DÃO ENTRADA NO ESTOQUE quando há produto
cadastrado. Sem isso, "aproveitamos a casca" é uma frase no relatório: o
almoxarifado não sabe que ela existe, ninguém a consome de lugar nenhum, e no
mês seguinte se compra ração que já estava no pátio.
"""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel

ZERO = Decimal('0')


class Subproduto(FilialScopedModel):
    """Um subproduto ou resíduo gerado por uma ordem de produção."""

    class Tipo(models.TextChoices):
        CASCA = 'casca', 'Casca'
        SEMENTE = 'semente', 'Semente / caroço'
        BAGACO = 'bagaco', 'Bagaço'
        FORA_PADRAO = 'fora_padrao', 'Fruta fora do padrão'
        RESIDUO = 'residuo', 'Resíduo de processamento'
        OUTRO = 'outro', 'Outro'

    class Destino(models.TextChoices):
        # A ordem é a do valor que cada destino devolve: reaproveitar é o
        # melhor, descartar é o pior — e ver a lista nessa ordem já sugere
        # subir uma linha.
        REAPROVEITAMENTO = 'reaproveitamento', 'Reaproveitamento'
        VENDA = 'venda', 'Venda'
        USO_INTERNO = 'uso_interno', 'Uso interno'
        DOACAO = 'doacao', 'Doação'
        DESCARTE = 'descarte', 'Descarte'

    # Destinos em que o material CONTINUA NA CASA e por isso precisa existir
    # no estoque. Venda e doação saem para terceiros; descarte deixa de ser
    # material e vira despesa de destinação.
    DESTINOS_QUE_ENTRAM = (Destino.REAPROVEITAMENTO, Destino.USO_INTERNO)

    ordem = models.ForeignKey(
        'polpa.OrdemPolpa', on_delete=models.CASCADE, related_name='subprodutos',
    )
    # De qual etapa saiu. Opcional porque nem toda casa aponta etapa a etapa,
    # mas quando existe é o que liga o peso à perda que já foi medida.
    etapa = models.ForeignKey(
        'polpa.ApontamentoEtapa', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='subprodutos',
    )

    tipo = models.CharField(max_length=14, choices=Tipo.choices, db_index=True)
    descricao = models.CharField(
        max_length=120, blank=True,
        help_text='Quando "outro", ou para detalhar: "casca com polpa aderida".',
    )

    quantidade = models.DecimalField(
        max_digits=12, decimal_places=3,
        validators=[MinValueValidator(ZERO)],
    )
    unidade = models.CharField(max_length=6, default='kg')

    destino = models.CharField(
        max_length=18, choices=Destino.choices, db_index=True,
    )
    # Para quem foi: o pecuarista que levou o bagaço, a instituição que
    # recebeu a doação, a empresa de coleta. Texto e não FK: quem leva resíduo
    # raramente está no cadastro de clientes, e exigir cadastro faria o
    # registro deixar de ser feito.
    destinatario = models.CharField(max_length=120, blank=True)

    # O DINHEIRO TEM SINAL. Venda entra, destinação sai -- e guardar os dois
    # no mesmo campo com sinais opostos faria alguém somar tudo e achar que a
    # fábrica lucrou com o lixo.
    valor_recebido = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO,
        validators=[MinValueValidator(ZERO)],
        help_text='O que entrou pela venda deste subproduto.',
    )
    custo_destinacao = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO,
        validators=[MinValueValidator(ZERO)],
        help_text='O que se pagou para dar fim a ele — coleta, aterro, frete.',
    )

    # Quando o subproduto é um item de catálogo, o reaproveitamento dá entrada
    # de verdade no estoque em vez de virar frase de relatório.
    produto_estoque = models.ForeignKey(
        'produtos.Produto', on_delete=models.PROTECT, null=True, blank=True,
        related_name='subprodutos_polpa',
        help_text='Cadastre para que o reaproveitamento entre no estoque.',
    )
    estoque_creditado_em = models.DateTimeField(null=True, blank=True, editable=False)

    data = models.DateField()
    observacao = models.TextField(blank=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='subprodutos_polpa',
    )

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_subprodutos'
        ordering = ['-data', '-pk']
        indexes = [
            models.Index(fields=['filial', 'destino']),
            models.Index(fields=['ordem']),
            models.Index(fields=['tipo', 'destino']),
        ]
        verbose_name = 'Subproduto ou resíduo'
        verbose_name_plural = 'Subprodutos e resíduos'

    def __str__(self):
        return (
            f'{self.quantidade} {self.unidade} de {self.get_tipo_display()} '
            f'— {self.get_destino_display()}'
        )

    # ── Leitura ──────────────────────────────────────────────────────────

    @property
    def resultado(self) -> Decimal:
        """
        O que este subproduto deixou no caixa. Negativo quando custou.

        É a conta que separa bagaço vendido de bagaço no caminhão da
        prefeitura — os dois pesam igual e são o oposto um do outro aqui.
        """
        return (self.valor_recebido or ZERO) - (self.custo_destinacao or ZERO)

    @property
    def entra_no_estoque(self) -> bool:
        return self.destino in self.DESTINOS_QUE_ENTRAM

    @property
    def creditado(self) -> bool:
        return self.estoque_creditado_em is not None

    @property
    def pendente_de_credito(self) -> bool:
        """
        Fica no estoque mas ainda não entrou nele.

        É a lacuna que faz alguém comprar ração que já está no pátio: o
        material foi separado para uso interno, e o almoxarifado não sabe.
        """
        return (
            self.entra_no_estoque
            and self.produto_estoque_id is not None
            and not self.creditado
        )

    @property
    def rotulo(self) -> str:
        return self.descricao.strip() or self.get_tipo_display()
