"""
Ordem de Produção — o documento que desce para a fábrica.

Nasce do pedido e aponta para ele. Quase tudo que a OP mostra é LIDO das
origens (cliente do pedido, grade e personalização do item, ficha e roteiro
do produto) em vez de copiado: cópia envelhece calada, e uma OP que mostra
a arte antiga depois que o cliente mandou trocar é pior do que não mostrar
arte nenhuma.

O que a OP COPIA são só os quatro campos que ela passa a governar:
quantidade, prazo, prioridade e observações. São o compromisso que a
fábrica assumiu quando a ordem foi emitida — se mudassem sozinhos junto com
o pedido, o corte já feito viraria erro sem ninguém ter decidido nada.

E porque copia, ela compara: quando a quantidade ou o prazo do pedido muda
depois da emissão, a divergência aparece na tela. Nem sobrescreve calada,
nem esconde.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models.base import FilialManager, FilialScopedModel


def _data(dia) -> str:
    """Data para mensagem, ou 'sem prazo' — evita 'None' na tela."""
    return f'{dia:%d/%m/%Y}' if dia else 'sem prazo'


class OrdemProducao(FilialScopedModel):

    class Status(models.TextChoices):
        # Ordem do fluxo, não alfabética — é a fila que a fábrica percorre.
        EMITIDA = 'emitida', 'Emitida'
        LIBERADA = 'liberada', 'Liberada'
        EM_PRODUCAO = 'em_producao', 'Em Produção'
        CONCLUIDA = 'concluida', 'Concluída'
        CANCELADA = 'cancelada', 'Cancelada'

    # Encerradas: não entram na fila da fábrica e liberam o item para uma
    # nova OP, se for preciso reemitir.
    STATUS_ENCERRADOS = (Status.CONCLUIDA, Status.CANCELADA)

    class Prioridade(models.TextChoices):
        NORMAL = 'normal', 'Normal'
        ALTA = 'alta', 'Alta'
        URGENTE = 'urgente', 'Urgente'

    numero = models.CharField(max_length=20, db_index=True)
    # Ano e sequencial guardados separados do número: reconstruir a
    # numeração a partir do texto exigiria fatiar string a cada emissão, e
    # um formato diferente no passado quebraria a conta.
    ano = models.PositiveSmallIntegerField()
    sequencial = models.PositiveIntegerField()

    pedido = models.ForeignKey(
        'moda.PedidoProducao', on_delete=models.PROTECT, related_name='ordens',
    )
    item = models.ForeignKey(
        'moda.ItemPedidoProducao', on_delete=models.PROTECT, related_name='ordens',
        help_text='Uma OP por produto do pedido — é o que caminha pela fábrica.',
    )

    # ── Os quatro campos que a OP governa ────────────────────────────────
    quantidade = models.PositiveIntegerField()
    prazo = models.DateField(null=True, blank=True)
    prioridade = models.CharField(
        max_length=10, choices=Prioridade.choices, default=Prioridade.NORMAL,
    )
    observacoes = models.TextField(blank=True)

    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.EMITIDA, db_index=True,
    )

    emitida_em = models.DateTimeField(default=timezone.now)
    emitida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='ordens_moda',
    )

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_ordens_producao'
        ordering = ['-ano', '-sequencial']
        unique_together = [('filial', 'ano', 'sequencial')]
        indexes = [
            models.Index(fields=['filial', 'status']),
            models.Index(fields=['filial', 'prazo']),
        ]
        verbose_name = 'Ordem de produção'
        verbose_name_plural = 'Ordens de produção'

    def __str__(self):
        return self.numero

    # ── Numeração ────────────────────────────────────────────────────────

    @staticmethod
    def montar_numero(ano: int, sequencial: int) -> str:
        return f'OP-{ano}-{sequencial:06d}'

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = self.montar_numero(self.ano, self.sequencial)
        super().save(*args, **kwargs)

    # ── Leituras das origens ─────────────────────────────────────────────

    @property
    def cliente(self):
        return self.pedido.cliente

    @property
    def produto(self):
        """Produto de catálogo do item, ou None quando é descrição livre."""
        return self.item.produto

    @property
    def descricao_produto(self) -> str:
        return self.item.nome_exibicao

    @property
    def grade(self):
        """Distribuição por tamanho — o que o corte precisa saber."""
        return self.item.grade.select_related('tamanho').all()

    @property
    def personalizacoes(self):
        return self.item.personalizacoes.all()

    @property
    def visuais(self):
        """As artes/mockups posicionados na peça."""
        return self.item.visuais.select_related('mockup').all()

    @property
    def individuais(self):
        """Nome e número por pessoa, quando o pedido é personalizado."""
        return self.item.individuais.select_related('tamanho').all()

    @property
    def ficha(self):
        produto = self.produto
        return getattr(produto, 'ficha', None) if produto else None

    @property
    def materiais(self):
        ficha = self.ficha
        return ficha.materiais.all() if ficha else []

    @property
    def roteiro(self):
        produto = self.produto
        return getattr(produto, 'roteiro', None) if produto else None

    @property
    def operacoes(self):
        roteiro = self.roteiro
        return roteiro.etapas.select_related('operacao').all() if roteiro else []

    @property
    def materiais_da_ordem(self) -> list[dict]:
        """
        Materiais com o total para ESTA ordem, não para uma peça.

        A conta fica aqui e não no template porque `{% widthratio %}`
        arredonda para inteiro: 1,296 m × 40 peças viraria 52 m em vez de
        51,84 m, e a diferença some direto na requisição ao estoque.
        """
        return [
            {
                'material': m,
                'total': (m.consumo_bruto * self.quantidade).quantize(Decimal('0.0001')),
            }
            for m in self.materiais
        ]

    @property
    def operacoes_da_ordem(self) -> list[dict]:
        """Etapas com o tempo total desta ordem, pelo mesmo motivo acima."""
        return [
            {
                'etapa': e,
                'minutos': (e.tempo * self.quantidade).quantize(Decimal('0.01')),
            }
            for e in self.operacoes
        ]

    # ── Custo e tempo previstos para ESTA ordem ──────────────────────────

    @property
    def custo_materiais(self) -> Decimal:
        """Materiais da peça × quantidade desta ordem."""
        ficha = self.ficha
        if ficha is None:
            return Decimal('0')
        return (ficha.custo_estimado * self.quantidade).quantize(Decimal('0.01'))

    @property
    def custo_mao_de_obra(self) -> Decimal:
        roteiro = self.roteiro
        if roteiro is None:
            return Decimal('0')
        return (roteiro.custo_total * self.quantidade).quantize(Decimal('0.01'))

    @property
    def custo_total(self) -> Decimal:
        return (self.custo_materiais + self.custo_mao_de_obra).quantize(Decimal('0.01'))

    @property
    def tempo_total_minutos(self) -> Decimal:
        roteiro = self.roteiro
        if roteiro is None:
            return Decimal('0')
        return (roteiro.tempo_total * self.quantidade).quantize(Decimal('0.01'))

    # ── Situação ─────────────────────────────────────────────────────────

    @property
    def encerrada(self) -> bool:
        return self.status in self.STATUS_ENCERRADOS

    @property
    def dias_para_prazo(self):
        if not self.prazo or self.encerrada:
            return None
        return (self.prazo - timezone.localdate()).days

    @property
    def atrasada(self) -> bool:
        dias = self.dias_para_prazo
        return dias is not None and dias < 0

    @property
    def divergencias(self) -> list[str]:
        """
        Onde a OP já não bate com o pedido.

        Existe porque a OP copia quantidade e prazo. Sem esta comparação, o
        pedido poderia ser alterado depois da emissão e a fábrica seguiria
        produzindo o número antigo sem que ninguém soubesse — que é
        exatamente o erro que copiar deveria evitar, e não causar.
        """
        avisos = []
        if self.quantidade != self.item.quantidade:
            avisos.append(
                f'A ordem tem {self.quantidade} peça(s), mas o item do pedido '
                f'agora tem {self.item.quantidade}.'
            )
        if self.prazo != self.pedido.data_prevista_entrega:
            avisos.append(
                'O prazo da ordem ({}) não é mais o do pedido ({}).'.format(
                    _data(self.prazo), _data(self.pedido.data_prevista_entrega),
                )
            )
        if self.prioridade != self.pedido.prioridade:
            avisos.append(
                f'A prioridade da ordem é {self.get_prioridade_display()}, '
                f'e a do pedido é {self.pedido.get_prioridade_display()}.'
            )
        return avisos
