"""
A receita da fábrica — o que a ficha técnica do ERP não sabia responder.

NÃO É UMA SEGUNDA FICHA TÉCNICA. `producao.FichaTecnica` já existe, já é
quem a ordem de produção lê, e já guarda produto acabado, versão,
quantidade produzida, tempo de produção, custo padrão de mão de obra e
indireto, status e os ITENS com quantidade e perda prevista. A ordem de
produção, a rastreabilidade e o painel de analytics apontam para ela — uma
receita paralela aqui significaria produzir por uma e custear pela outra.

O QUE FALTAVA É O QUE A POLPA TEM E UMA MONTAGEM DE PARAFUSO NÃO TEM:

  · RENDIMENTO. Numa linha de montagem, 10 peças entram e 10 saem. Aqui
    1.000 kg de manga viram 600 kg de polpa: casca e caroço saem no meio do
    processo. Sem o rendimento ESPERADO gravado, a perda real não tem contra
    o que ser comparada — e perda sem comparação vira "normal";

  · AS ETAPAS, EM ORDEM, COM TEMPERATURA. Pasteurizar a 92°C por 30 segundos
    e a 85°C por 2 minutos são produtos diferentes, com validades
    diferentes. Guardar isso num campo de texto livre é o que faz a
    instrução se perder quando a pessoa que sabia sai de férias;

  · O EQUIPAMENTO de cada etapa. É o que diz se duas receitas disputam a
    mesma despolpadeira — a pergunta do PCP antes de prometer prazo.

VERSÃO É REGISTRO, NÃO EDIÇÃO. Mudar a receita que já produziu lotes
apagaria a explicação do que foi feito naqueles lotes: a fórmula de hoje
diria uma coisa e o produto na câmara seria outra. Por isso alterar receita
ativa é criar uma versão nova, e só UMA fica ativa por produto — a que a
ordem de produção usa.
"""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel, TimestampedModel

ZERO = Decimal('0')
CEM = Decimal('100')


class Receita(FilialScopedModel):
    """A ficha técnica do ERP, com o que a fábrica de polpa precisa."""

    # UM PARA UM com a ficha do ERP: duas receitas para a mesma ficha seriam
    # duas respostas para "como este produto é feito".
    ficha = models.OneToOneField(
        'producao.FichaTecnica', on_delete=models.CASCADE, related_name='receita_polpa',
    )

    # ── Rendimento ───────────────────────────────────────────────────────
    # Nulo é "ninguém definiu". Zero seria uma receita que não rende nada, e
    # a comparação com o real acusaria 100% de perda em toda produção.
    rendimento_esperado = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text='% do que entra que vira produto. Ex.: manga ≈ 60%.',
    )

    # ── Processo ─────────────────────────────────────────────────────────
    temperatura_processo_min = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
    )
    temperatura_processo_max = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
    )
    observacoes_tecnicas = models.TextField(
        blank=True,
        help_text='O que quem opera precisa saber e não cabe numa etapa.',
    )

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_receitas'
        ordering = ['ficha__produto_acabado', '-ficha__versao']
        verbose_name = 'Receita'
        verbose_name_plural = 'Receitas'

    def __str__(self):
        return f'{self.ficha}'

    # ── Atalhos para a ficha ─────────────────────────────────────────────

    @property
    def produto(self):
        return self.ficha.produto_acabado

    @property
    def versao(self) -> str:
        return self.ficha.versao

    @property
    def ativa(self) -> bool:
        from apps.producao.models import FichaTecnica

        return self.ficha.status == FichaTecnica.Status.ATIVA

    @property
    def rendimento_por_etapa(self) -> Decimal:
        """
        O rendimento composto das etapas, quando cada uma declara a sua perda.

        SERVE DE CONFERÊNCIA para o rendimento digitado no topo: uma receita
        que perde 25% na despolpa e 5% no envase não rende 70%, rende 71,25%
        — as perdas se aplicam em cascata, não se somam. Ver as duas contas
        lado a lado é o que revela a estimativa feita de cabeça.
        """
        fator = Decimal('1')
        for etapa in self.etapas.all():
            if etapa.perda_percentual:
                fator *= (CEM - etapa.perda_percentual) / CEM
        return (fator * CEM).quantize(Decimal('0.01'))

    def pendencias(self) -> list[str]:
        """O que falta para a receita poder virar produção."""
        faltando = []
        itens = list(self.ficha.itens.all())

        if not itens:
            faltando.append('Sem ingredientes lançados — não há o que consumir.')
        if not self.rendimento_esperado:
            faltando.append(
                'Sem rendimento esperado — a perda real não terá contra o que '
                'ser comparada.'
            )
        if not self.ficha.quantidade_produzida:
            faltando.append(
                'Sem quantidade produzida por batida — o custo unitário não fecha.'
            )
        if not self.etapas.exists():
            faltando.append('Sem etapas do processo — a instrução fica na memória.')
        return faltando


class EtapaReceita(TimestampedModel):
    """Uma etapa do processo, na ordem em que acontece."""

    receita = models.ForeignKey(
        Receita, on_delete=models.CASCADE, related_name='etapas',
    )
    ordem = models.PositiveSmallIntegerField(
        default=1, help_text='A sequência importa: sanitizar depois de despolpar não sanitiza.',
    )
    nome = models.CharField(max_length=80)
    # A ETAPA CANÔNICA, quando esta corresponde a uma das dezoito do
    # processo. É o elo entre o PLANO (a receita) e o FATO (o apontamento
    # da ordem): sem ele, "despolpa", "despolpamento" e "polpação" viram
    # três etapas diferentes no relatório e o rendimento deixa de somar.
    # Em branco continua valendo como instrução — só não vira apontamento.
    etapa = models.CharField(
        max_length=20, blank=True,
        help_text='A etapa do processo de polpa que esta corresponde.',
    )
    equipamento = models.CharField(
        max_length=80, blank=True,
        help_text='Despolpadeira, pasteurizador, envasadora, túnel…',
    )

    tempo_minutos = models.PositiveIntegerField(
        null=True, blank=True, help_text='Duração estimada desta etapa.',
    )
    temperatura_min = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
    )
    temperatura_max = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
    )
    # A PERDA É POR ETAPA, e não só no total: saber que 22% se perdem na
    # despolpa e 3% no envase é o que diz ONDE atacar. Um total de 25% não
    # diz em qual máquina mexer.
    perda_percentual = models.DecimalField(
        max_digits=5, decimal_places=2, default=ZERO,
        validators=[MinValueValidator(0)],
        help_text='% do que entra nesta etapa que se perde nela.',
    )
    instrucao = models.TextField(blank=True)

    class Meta:
        db_table = 'polpa_etapas_receita'
        ordering = ['ordem', 'id']
        verbose_name = 'Etapa da receita'
        verbose_name_plural = 'Etapas da receita'

    def __str__(self):
        return f'{self.ordem}. {self.nome}'

    @property
    def faixa_temperatura(self) -> str:
        """A faixa em texto — vazia quando ninguém definiu."""
        if self.temperatura_min is None and self.temperatura_max is None:
            return ''
        if self.temperatura_min is not None and self.temperatura_max is not None:
            return f'{self.temperatura_min}°C a {self.temperatura_max}°C'
        valor = self.temperatura_min if self.temperatura_min is not None else self.temperatura_max
        return f'{valor}°C'
