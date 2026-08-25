"""
O processo: as etapas de cada produto, e o que aconteceu em cada uma.

DUAS COISAS DIFERENTES QUE TODO MUNDO CHAMA DE "ETAPA":

  · O QUE A RECEITA MANDA FAZER — pasteurizar a 92°C por 30 s. Isso é
    plano, vale para toda produção daquele produto, e mora na receita
    (`EtapaReceita`, da ficha técnica);

  · O QUE ACONTECEU NESTA BATIDA — às 6h47 o João despolpou 980 kg na
    despolpadeira 2 e sobraram 610. Isso é fato, vale para uma ordem só, e
    mora aqui.

Misturar os dois é o erro clássico: a receita passa a ser reescrita a cada
produção (e some a fórmula) ou o apontamento vira campo de texto (e some a
conta). Por isso são dois modelos, ligados pela ETAPA CANÔNICA.

O VOCABULÁRIO É FIXO, e não um cadastro livre: o nome de cada etapa precisa
ser o mesmo em toda parte, senão "despolpa", "despolpamento" e "polpação"
viram três etapas diferentes nos relatórios e o rendimento por etapa deixa
de somar.

O CAMINHO, PORÉM, É POR PRODUTO. Polpa passa por despolpamento e refino;
açaí passa por processamento, mistura e resfriamento — e nenhum dos dois faz
o que o outro faz. Um fluxo único com "etapas que não se aplicam" encheria a
tela de linha morta, e linha morta faz a pessoa parar de olhar a lista.

O RENDIMENTO NASCE DA DIFERENÇA entre o que entrou e o que saiu de cada
etapa. É a única forma de responder "onde a fruta se perde" — um total de
40% de perda não diz em qual máquina mexer.
"""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel

ZERO = Decimal('0')


class Etapa(models.TextChoices):
    """O vocabulário das etapas, na ordem em que acontecem."""

    RECEPCAO = 'recepcao', 'Recepção da matéria-prima'
    PESAGEM = 'pesagem', 'Pesagem'
    INSPECAO = 'inspecao', 'Inspeção'
    PESAGEM_INGREDIENTES = 'pesagem_ingredientes', 'Pesagem dos ingredientes'
    SELECAO = 'selecao', 'Seleção'
    LAVAGEM = 'lavagem', 'Lavagem'
    HIGIENIZACAO = 'higienizacao', 'Higienização'
    SANITIZACAO = 'sanitizacao', 'Sanitização'
    DESCASCAMENTO = 'descascamento', 'Descascamento'
    CORTE = 'corte', 'Corte'
    DESPOLPAMENTO = 'despolpamento', 'Despolpamento'
    PROCESSAMENTO = 'processamento', 'Processamento'
    REFINO = 'refino', 'Peneiramento / refino'
    FORMULACAO = 'formulacao', 'Formulação'
    PREPARO_CALDA = 'preparo_calda', 'Preparação da calda/base'
    MISTURA = 'mistura', 'Mistura'
    PASTEURIZACAO = 'pasteurizacao', 'Pasteurização'
    HOMOGENEIZACAO = 'homogeneizacao', 'Homogeneização'
    MATURACAO = 'maturacao', 'Maturação'
    SABORIZACAO = 'saborizacao', 'Adição de saborizantes'
    INCORPORACAO_AR = 'incorporacao_ar', 'Incorporação de ar (overrun)'
    INCLUSOES = 'inclusoes', 'Adição de inclusões'
    RESFRIAMENTO = 'resfriamento', 'Resfriamento'
    ENVASE = 'envase', 'Envase'
    INSERCAO_PALITO = 'insercao_palito', 'Inserção do palito'
    ENDURECIMENTO = 'endurecimento', 'Endurecimento'
    SELAGEM = 'selagem', 'Selagem'
    IDENTIFICACAO = 'identificacao', 'Identificação do lote'
    CONGELAMENTO = 'congelamento', 'Congelamento rápido'
    DESENFORME = 'desenforme', 'Desenforme'
    EMBALAGEM = 'embalagem', 'Embalagem'
    EMPACOTAMENTO = 'empacotamento', 'Empacotamento'
    ARMAZENAMENTO = 'armazenamento', 'Armazenamento em câmara fria'
    LIBERACAO = 'liberacao', 'Liberação pelo controle de qualidade'


# A ORDEM É O PROCESSO. É ela que numera as etapas de uma ordem nova, e a
# sequência importa — sanitizar depois de despolpar não sanitiza.
SEQUENCIA: tuple[str, ...] = tuple(e.value for e in Etapa)
POSICAO = {etapa: i for i, etapa in enumerate(SEQUENCIA)}


# ══════════════════════════════════════════════════════════════════════
# OS FLUXOS
# ══════════════════════════════════════════════════════════════════════
#
# CADA PRODUTO TEM O SEU CAMINHO, e não é o mesmo. Polpa passa por
# despolpamento e refino; açaí passa por processamento, mistura e
# resfriamento -- e nenhum dos dois faz o que o outro faz. Um fluxo único
# com "etapas que não se aplicam" encheria a tela de linha morta, e linha
# morta é o que faz a pessoa parar de olhar a lista.
#
# A LISTA É POR TIPO DE PRODUTO (o `FichaProduto.Tipo` da seção 1), e a
# receita continua podendo declarar a sua -- o cadastro manda, o padrão só
# evita começar do zero.

FLUXO_POLPA: tuple[str, ...] = (
    Etapa.RECEPCAO, Etapa.PESAGEM, Etapa.INSPECAO, Etapa.SELECAO,
    Etapa.LAVAGEM, Etapa.SANITIZACAO, Etapa.DESCASCAMENTO, Etapa.CORTE,
    Etapa.DESPOLPAMENTO, Etapa.REFINO, Etapa.FORMULACAO,
    Etapa.HOMOGENEIZACAO, Etapa.ENVASE, Etapa.SELAGEM, Etapa.IDENTIFICACAO,
    Etapa.CONGELAMENTO, Etapa.ARMAZENAMENTO, Etapa.LIBERACAO,
)

FLUXO_ACAI: tuple[str, ...] = (
    Etapa.RECEPCAO, Etapa.SELECAO, Etapa.HIGIENIZACAO, Etapa.PROCESSAMENTO,
    Etapa.FORMULACAO, Etapa.MISTURA, Etapa.HOMOGENEIZACAO,
    Etapa.PASTEURIZACAO, Etapa.RESFRIAMENTO, Etapa.ENVASE,
    Etapa.CONGELAMENTO, Etapa.ARMAZENAMENTO, Etapa.LIBERACAO,
)

FLUXO_SORVETE: tuple[str, ...] = (
    Etapa.PESAGEM_INGREDIENTES, Etapa.MISTURA, Etapa.PREPARO_CALDA,
    Etapa.PASTEURIZACAO, Etapa.HOMOGENEIZACAO, Etapa.MATURACAO,
    Etapa.SABORIZACAO, Etapa.CONGELAMENTO, Etapa.INCORPORACAO_AR,
    Etapa.INCLUSOES, Etapa.ENVASE, Etapa.ENDURECIMENTO, Etapa.ARMAZENAMENTO,
)

FLUXO_PICOLE: tuple[str, ...] = (
    Etapa.PREPARO_CALDA, Etapa.FORMULACAO, Etapa.MISTURA, Etapa.ENVASE,
    Etapa.INSERCAO_PALITO, Etapa.CONGELAMENTO, Etapa.DESENFORME,
    Etapa.EMBALAGEM, Etapa.EMPACOTAMENTO, Etapa.ARMAZENAMENTO,
)

# ETAPAS QUE NEM TODA FÁBRICA FAZ. Ficam de fora da lista que uma ordem
# nova recebe, e entram quando a receita as declara: descascamento de
# acerola não existe, e pasteurização de açaí é "quando aplicável" -- há
# quem congele sem pasteurizar.
# O QUE É "QUANDO APLICÁVEL" DEPENDE DO FLUXO, e não é o mesmo em todos:
# formulação é opcional numa polpa (a de manga pura não formula nada) e
# OBRIGATÓRIA num picolé, que é calda formulada por definição. Uma lista
# única faria o picolé nascer sem a etapa que ele mais tem.
OPCIONAIS_POR_FLUXO = {
    'polpa': (Etapa.DESCASCAMENTO, Etapa.CORTE, Etapa.FORMULACAO),
    'acai': (Etapa.PASTEURIZACAO,),
    # Massa de picolé não incorpora ar, e sorvete sem pedaço não tem
    # inclusão -- a receita declara quando existem.
    'sorvete': (Etapa.INCORPORACAO_AR, Etapa.INCLUSOES),
    'picole': (),
}

# A união, para quem precisa saber o que é opcional em algum lugar.
OPCIONAIS = tuple({
    etapa for lista in OPCIONAIS_POR_FLUXO.values() for etapa in lista
})

FLUXOS = {
    'polpa': FLUXO_POLPA,
    'acai': FLUXO_ACAI,
    # Sorvete, picolé e creme herdam o caminho do açaí enquanto não têm o
    # seu: batem em quase tudo (mistura, homogeneização, resfriamento,
    # envase, congelamento) e é melhor começar de um caminho parecido do
    # que de uma lista genérica que ninguém reconhece.
    'sorvete': FLUXO_SORVETE,
    'picole': FLUXO_PICOLE,
    'creme': FLUXO_ACAI,
    'mix': FLUXO_POLPA,
    'fruta_congelada': FLUXO_POLPA,
}


def fluxo_do_tipo(tipo: str, com_opcionais: bool = False) -> tuple[str, ...]:
    """O caminho de um tipo de produto. Polpa é o padrão de quem não tem."""
    chave = tipo if tipo in FLUXOS else 'polpa'
    fluxo = FLUXOS[chave]
    if com_opcionais:
        return fluxo
    opcionais = {e.value for e in OPCIONAIS_POR_FLUXO.get(chave, ())}
    return tuple(e for e in fluxo if e not in opcionais)


def fluxo_do_produto(produto, com_opcionais: bool = False) -> tuple[str, ...]:
    """O caminho deste produto, pela ficha da fábrica (seção 1)."""
    ficha = getattr(produto, 'ficha_polpa', None)
    return fluxo_do_tipo(getattr(ficha, 'tipo', ''), com_opcionais)


# Compatibilidade: `PADRAO` era a lista única antes de existirem fluxos.
PADRAO = fluxo_do_tipo('polpa')


class ApontamentoEtapa(FilialScopedModel):
    """O que aconteceu numa etapa desta ordem."""

    class Situacao(models.TextChoices):
        PENDENTE = 'pendente', 'Não iniciada'
        EM_ANDAMENTO = 'em_andamento', 'Em andamento'
        CONCLUIDA = 'concluida', 'Concluída'
        PULADA = 'pulada', 'Não se aplica'

    ordem = models.ForeignKey(
        'polpa.OrdemPolpa', on_delete=models.CASCADE, related_name='etapas_processo',
    )
    etapa = models.CharField(max_length=20, choices=Etapa.choices, db_index=True)
    sequencia = models.PositiveSmallIntegerField(default=0)

    situacao = models.CharField(
        max_length=15, choices=Situacao.choices,
        default=Situacao.PENDENTE, db_index=True,
    )

    # ── Quando e quem ────────────────────────────────────────────────────
    iniciada_em = models.DateTimeField(null=True, blank=True)
    concluida_em = models.DateTimeField(null=True, blank=True)
    operador = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='etapas_polpa',
        help_text='Quem executou — a pergunta que a auditoria faz primeiro.',
    )
    equipamento = models.ForeignKey(
        'polpa.Recurso', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='apontamentos',
    )

    # ── Quanto ───────────────────────────────────────────────────────────
    # Entrada e saída, e não "quantidade processada" sozinha: é a diferença
    # entre as duas que diz onde a fruta se perde.
    quantidade_entrada = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    quantidade_saida = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    motivo_perda = models.CharField(
        max_length=160, blank=True,
        help_text='Casca e caroço, fruta descartada na seleção, sobra de linha…',
    )

    # VOLUME AO LADO DO PESO, e não no lugar dele. Sorvete se vende em
    # litro e se produz em quilo: a mesma batida tem 100 kg de base e sai
    # com 180 litros de sorvete. Guardar um só faria a fábrica converter de
    # cabeça a cada apontamento -- e o overrun, que é a razão entre os dois,
    # ficaria impossível de calcular.
    volume_entrada = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(0)], help_text='Litros que entraram.',
    )
    volume_saida = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(0)], help_text='Litros que saíram.',
    )

    temperatura = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text='°C medidos nesta etapa.',
    )

    # ── Rastro ───────────────────────────────────────────────────────────
    lote = models.ForeignKey(
        'estoque.LoteProduto', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='etapas_polpa',
        help_text='Lote de matéria-prima consumido ou de produto gerado aqui.',
    )
    observacao = models.TextField(blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_apontamentos_etapa'
        ordering = ['ordem', 'sequencia', 'id']
        unique_together = [('ordem', 'etapa')]
        indexes = [
            models.Index(fields=['filial', 'situacao']),
            models.Index(fields=['ordem', 'sequencia']),
        ]
        verbose_name = 'Etapa apontada'
        verbose_name_plural = 'Etapas apontadas'

    def __str__(self):
        return f'{self.ordem.numero} — {self.get_etapa_display()}'

    # ── Leituras ─────────────────────────────────────────────────────────

    @property
    def perda(self) -> Decimal | None:
        """
        Entrada menos saída. `None` quando falta um dos dois.

        Zero seria "não perdeu nada", e é diferente de "ninguém pesou" — a
        segunda é a situação normal em etapa que ainda não foi apontada, e
        confundir as duas faria o relatório de perdas mentir para menos.
        """
        if self.quantidade_entrada is None or self.quantidade_saida is None:
            return None
        return max(self.quantidade_entrada - self.quantidade_saida, ZERO)

    @property
    def perda_percentual(self) -> Decimal | None:
        perda = self.perda
        if perda is None or not self.quantidade_entrada:
            return None
        return (perda / self.quantidade_entrada * 100).quantize(Decimal('0.01'))

    @property
    def rendimento(self) -> Decimal | None:
        """Quanto do que entrou saiu — o oposto da perda."""
        percentual = self.perda_percentual
        return None if percentual is None else (Decimal('100') - percentual)

    @property
    def overrun(self) -> Decimal | None:
        """
        Quanto de ar entrou, em percentual do que havia antes.

        O NÚMERO QUE DEFINE O SORVETE. 100 litros de base que viram 180 têm
        80% de overrun: é o que separa um sorvete cremoso de um bloco de
        gelo, e é o que decide quantos potes saem de uma batida -- ou seja,
        a margem. Sorvete artesanal fica entre 20% e 50%; industrial passa
        de 100%.

        Calculado pelo VOLUME quando ele existe, e pelo peso quando não:
        overrun é ganho de volume, e medir por peso só funciona porque a
        massa não muda — o ar não pesa.

        `None` sem as duas medidas: zero seria "não incorporou ar nenhum",
        que é uma afirmação sobre o produto, não a ausência de medição.
        """
        antes = self.volume_entrada or self.quantidade_entrada
        depois = self.volume_saida or self.quantidade_saida
        if not antes or depois is None or antes <= ZERO:
            return None
        return ((depois - antes) / antes * 100).quantize(Decimal('0.01'))

    @property
    def exige_volume(self) -> bool:
        """Onde o litro é a medida que importa, não o quilo."""
        return self.etapa in (
            Etapa.INCORPORACAO_AR, Etapa.ENVASE, Etapa.MATURACAO,
        )

    @property
    def duracao_minutos(self) -> int | None:
        if not self.iniciada_em or not self.concluida_em:
            return None
        return int((self.concluida_em - self.iniciada_em).total_seconds() // 60)

    @property
    def apontada(self) -> bool:
        return self.situacao in (self.Situacao.EM_ANDAMENTO, self.Situacao.CONCLUIDA)

    @property
    def exige_temperatura(self) -> bool:
        """
        As etapas em que a temperatura NÃO é detalhe.

        Congelamento e armazenamento são a cadeia de frio; recepção diz em
        que estado a fruta chegou. Cobrar temperatura em "seleção" seria
        burocracia; deixar de cobrar no congelamento é perder o registro que
        a fiscalização pede.
        """
        return self.etapa in (
            Etapa.RECEPCAO, Etapa.CONGELAMENTO, Etapa.ARMAZENAMENTO,
            Etapa.HOMOGENEIZACAO, Etapa.PASTEURIZACAO, Etapa.MATURACAO,
            Etapa.RESFRIAMENTO, Etapa.ENDURECIMENTO,
        )
