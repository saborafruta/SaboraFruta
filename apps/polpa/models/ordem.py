"""
A ordem de produção da fábrica de polpa.

DE NOVO, NÃO É UMA SEGUNDA OP. `producao.OrdemProducao` já existe, já é
quem consome matéria-prima por FEFO, cria o lote do produto acabado, dá
entrada no estoque e calcula custo e rendimento no encerramento. Uma ordem
paralela aqui produziria sem baixar estoque — e o saldo do ERP viraria
ficção.

O QUE FALTAVA SÃO OS ESTADOS DE UMA FÁBRICA DE ALIMENTO. A OP do ERP tem
cinco (rascunho, aberta, em produção, encerrada, cancelada) e nenhum deles
responde às duas perguntas que se ouvem no chão desta fábrica:

  · "PAROU POR QUÊ?" A linha para o tempo todo: acabou a fruta, quebrou a
    despolpadeira, faltou embalagem. Sem o estado PAUSADA, tudo isso é "em
    produção" — e a OP que está parada há seis horas tem a mesma cara da
    que está rodando;

  · "JÁ LIBEROU?" Entre produzir e vender existe a análise: o lote fica
    retido até o resultado sair. Sem o estado EM QUALIDADE, "produzida"
    significaria pronto para faturar, e é assim que um lote sai antes do
    laudo.

O MAPA PARA O ERP mora aqui, num lugar só. Cada estado da fábrica aponta
para o status que a OP do ERP entende, e é ele que o resto do sistema lê.
Dois estados podem mapear para o mesmo: pausada e em qualidade continuam
sendo "em produção" para o ERP, porque a OP de fato não terminou.
"""
from django.conf import settings
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel


class OrdemPolpa(FilialScopedModel):
    """A OP do ERP, com os estados e os dados da fábrica de polpa."""

    class Situacao(models.TextChoices):
        # A ORDEM É O PROCESSO. `Situacao.choices` alimenta a régua da tela
        # e a validação de avanço — trocar a ordem troca o processo.
        PLANEJADA = 'planejada', 'Planejada'
        LIBERADA = 'liberada', 'Liberada'
        EM_PRODUCAO = 'em_producao', 'Em produção'
        PAUSADA = 'pausada', 'Pausada'
        QUALIDADE = 'qualidade', 'Em controle de qualidade'
        PRODUZIDA = 'produzida', 'Produzida'
        CANCELADA = 'cancelada', 'Cancelada'

    ABERTAS = (
        Situacao.PLANEJADA, Situacao.LIBERADA, Situacao.EM_PRODUCAO,
        Situacao.PAUSADA, Situacao.QUALIDADE,
    )
    ENCERRADAS = (Situacao.PRODUZIDA, Situacao.CANCELADA)

    # Situação da fábrica → status que a OP do ERP entende. Pausada e
    # qualidade continuam "em produção" lá porque a ordem não terminou.
    STATUS_DO_ERP = {
        Situacao.PLANEJADA: 'rascunho',
        Situacao.LIBERADA: 'aberta',
        Situacao.EM_PRODUCAO: 'em_producao',
        Situacao.PAUSADA: 'em_producao',
        Situacao.QUALIDADE: 'em_producao',
        Situacao.PRODUZIDA: 'encerrada',
        Situacao.CANCELADA: 'cancelada',
    }

    # Para onde cada situação pode ir. Escrito como tabela porque a
    # alternativa -- um `if` em cada view -- é como se abre um caminho que
    # ninguém revisou (produzir sem liberar, por exemplo).
    TRANSICOES = {
        Situacao.PLANEJADA: (Situacao.LIBERADA, Situacao.CANCELADA),
        Situacao.LIBERADA: (Situacao.EM_PRODUCAO, Situacao.PLANEJADA, Situacao.CANCELADA),
        Situacao.EM_PRODUCAO: (Situacao.PAUSADA, Situacao.QUALIDADE, Situacao.PRODUZIDA, Situacao.CANCELADA),
        Situacao.PAUSADA: (Situacao.EM_PRODUCAO, Situacao.CANCELADA),
        Situacao.QUALIDADE: (Situacao.PRODUZIDA, Situacao.EM_PRODUCAO, Situacao.CANCELADA),
        Situacao.PRODUZIDA: (),
        Situacao.CANCELADA: (),
    }

    ordem = models.OneToOneField(
        'producao.OrdemProducao', on_delete=models.CASCADE,
        related_name='polpa',
    )
    # A RECEITA FICA PRESA NA OP. A ficha técnica já está na ordem do ERP,
    # mas é a VERSÃO dela que explica o lote seis meses depois -- e a versão
    # ativa de hoje pode não ser a que produziu.
    receita = models.ForeignKey(
        'polpa.Receita', on_delete=models.PROTECT, related_name='ordens',
    )

    situacao = models.CharField(
        max_length=15, choices=Situacao.choices,
        default=Situacao.PLANEJADA, db_index=True,
    )

    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='ordens_polpa',
        help_text='Quem responde por esta produção.',
    )

    # ── Pausa ────────────────────────────────────────────────────────────
    pausada_em = models.DateTimeField(null=True, blank=True)
    motivo_pausa = models.CharField(
        max_length=160, blank=True,
        help_text='Acabou a fruta, quebrou a despolpadeira, faltou embalagem…',
    )
    minutos_parados = models.PositiveIntegerField(
        default=0,
        help_text='Soma das pausas — o tempo que a linha ficou parada.',
    )

    # ── Qualidade ────────────────────────────────────────────────────────
    enviada_qualidade_em = models.DateTimeField(null=True, blank=True)
    liberada_qualidade_em = models.DateTimeField(null=True, blank=True)

    observacao = models.TextField(blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_ordens'
        ordering = ['-ordem__created_at']
        indexes = [
            models.Index(fields=['filial', 'situacao']),
        ]
        verbose_name = 'Ordem de produção'
        verbose_name_plural = 'Ordens de produção'

    def __str__(self):
        return f'{self.ordem.numero} — {self.produto}'

    # ── Atalhos para a OP do ERP ─────────────────────────────────────────

    @property
    def numero(self) -> str:
        return self.ordem.numero

    @property
    def produto(self):
        return self.ordem.produto_acabado

    @property
    def quantidade_planejada(self):
        return self.ordem.quantidade_planejada

    @property
    def quantidade_produzida(self):
        return self.ordem.quantidade_produzida

    @property
    def lote(self):
        return self.ordem.lote_gerado

    @property
    def validade(self):
        """A validade do lote produzido — nula enquanto não há lote."""
        return self.lote.data_validade if self.lote else None

    @property
    def encerrada(self) -> bool:
        return self.situacao in self.ENCERRADAS

    @property
    def em_andamento(self) -> bool:
        return self.situacao in self.ABERTAS

    def pode_ir_para(self, destino: str) -> bool:
        return destino in self.TRANSICOES.get(self.situacao, ())

    @property
    def proximos(self) -> list[tuple[str, str]]:
        """Para onde esta OP pode ir, com o rótulo — a tela lê daqui."""
        return [
            (valor, self.Situacao(valor).label)
            for valor in self.TRANSICOES.get(self.situacao, ())
        ]

    # ── Leituras ─────────────────────────────────────────────────────────

    @property
    def validade_prevista(self):
        """
        Quando o lote desta OP vai vencer, pelo prazo do produto.

        Aparece ANTES de produzir porque é o que o comercial precisa saber
        para prometer entrega — e porque uma validade prevista curta demais
        é a hora de descobrir que o prazo do produto está errado, não depois
        de o lote estar na câmara.
        """
        from django.utils import timezone

        ficha = getattr(self.produto, 'ficha_polpa', None)
        if ficha is None:
            return None
        base = self.ordem.data_fim_real or timezone.localdate()
        if hasattr(base, 'date'):
            base = base.date()
        return ficha.validade_a_partir_de(base)

    @property
    def atrasada(self) -> bool:
        """Passou da data prevista e ainda não terminou."""
        from django.utils import timezone

        previsto = self.ordem.data_fim_prevista
        return bool(
            previsto and self.em_andamento and previsto < timezone.now()
        )
