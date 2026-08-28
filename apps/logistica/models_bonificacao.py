"""
O acompanhamento da entrega de uma bonificação, e a prova de que ela chegou.

POR QUE A CORTESIA PRECISA DE ACOMPANHAMENTO PRÓPRIO

Mercadoria vendida cobra a si mesma: se não chegar, o cliente liga. A
bonificação não — ninguém pagou por ela, ninguém reclama, e ela é
exatamente a que some no caminho sem que nada acuse. É por isso que a
cortesia é o lugar clássico do desvio: sai do estoque com documento, e o
sistema nunca pergunta se chegou.

Este registro faz a pergunta. Enquanto a entrega não é marcada, a
bonificação fica PENDENTE e visível.

DUAS BONIFICAÇÕES, UM ACOMPANHAMENTO

A cortesia pode sair de dois lugares — uma linha da carga (endereçada antes
de o caminhão sair) ou uma entrega feita na rua. São origens diferentes do
mesmo fato, e por isso este modelo aponta para UMA das duas, nunca para as
duas nem para nenhuma: sem essa restrição, uma entrega órfã ficaria contada
no relatório sem ter o que a explique.

A PROVA É ANEXO, E NÃO CAMPO DE TEXTO

"Entregue: sim" não prova nada. Foto na porta, assinatura do canhoto,
comprovante — é isso que responde a "quem recebeu as 20 caixas?" seis meses
depois. Vários por entrega, porque a doca fotografa mais de uma coisa.

A CORTESIA QUE NÃO CHEGOU FICA IDENTIFICADA

Entre a recusa e o retorno tratado, a mercadoria está fora de qualquer
saldo: saiu do estoque, não virou entrega e ainda não voltou. Nesse
intervalo ela carrega o rótulo BONIFICAÇÃO NÃO ENTREGUE, com o motivo ao
lado — e é o serviço de entrega que faz a devolução acontecer, cada origem
para onde ela saiu.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models.base import TimestampedModel


def caminho_do_comprovante(instancia, nome_do_arquivo: str) -> str:
    """Um diretório por entrega — o arquivo segue a entrega, não a data."""
    return f'bonificacoes/{instancia.entrega_id}/{nome_do_arquivo}'


class EntregaBonificacao(TimestampedModel):
    """O acompanhamento de uma bonificação até a mão de quem recebe."""

    class Status(models.TextChoices):
        """
        O ciclo da cortesia.

        SÃO ETAPAS DE VERDADE. Entre "saiu" e "chegou" existe o caminho, e é
        nele que a bonificação some. Recusada não é o fim: a mercadoria está
        no caminhão e precisa voltar — daí `RETORNO_PENDENTE`, que é o único
        estado que cobra ação de alguém.
        """

        PENDENTE = 'pendente', 'Pendente'
        EM_TRANSPORTE = 'em_transporte', 'Em transporte'
        ENTREGUE = 'entregue', 'Entregue'
        RECUSADA = 'recusada', 'Recusada'
        CANCELADA = 'cancelada', 'Cancelada'
        RETORNO_PENDENTE = 'retorno_pendente', 'Retorno pendente'
        RETORNADA = 'retornada', 'Retornada'

    # AS TRANSIÇÕES SÃO EXPLÍCITAS, senão o status vira enfeite: alguém marca
    # "Entregue" numa bonificação que voltou, e o controle deixa de
    # significar coisa alguma.
    TRANSICOES = {
        Status.PENDENTE: (Status.EM_TRANSPORTE, Status.ENTREGUE, Status.CANCELADA),
        Status.EM_TRANSPORTE: (
            Status.ENTREGUE, Status.RECUSADA, Status.CANCELADA,
        ),
        # Recusada tem um destino só: a mercadoria está no caminhão.
        Status.RECUSADA: (Status.RETORNO_PENDENTE,),
        Status.RETORNO_PENDENTE: (Status.RETORNADA,),
        Status.ENTREGUE: (),
        Status.RETORNADA: (),
        Status.CANCELADA: (),
    }

    class MotivoNaoEntrega(models.TextChoices):
        """
        Por que a cortesia não chegou.

        LISTA FECHADA, PELA MESMA RAZÃO DO MOTIVO DA BONIFICAÇÃO: "por que
        não entregamos?" é pergunta que se responde por cliente e por rota,
        e frase digitada à mão não agrupa. A diferença entre "cliente
        ausente" (o roteiro está errado) e "produto danificado" (o
        carregamento está errado) é a diferença entre dois problemas que se
        resolvem em lugares diferentes.
        """

        AUSENTE = 'ausente', 'Cliente ausente'
        RECUSOU = 'recusou', 'Cliente recusou'
        DANIFICADO = 'danificado', 'Produto danificado'
        QUANTIDADE = 'quantidade', 'Erro de quantidade'
        CANCELAMENTO = 'cancelamento', 'Cancelamento'
        OUTRO = 'outro', 'Outro'

    ABERTAS = (Status.PENDENTE, Status.EM_TRANSPORTE, Status.RETORNO_PENDENTE)

    # A MERCADORIA QUE NAO CHEGOU E CONTINUA NO CAMINHAO. Enquanto ela esta'
    # nestes estados, e' "BONIFICACAO NAO ENTREGUE": saiu do estoque, nao
    # virou entrega e ainda nao voltou.
    NAO_ENTREGUES = (Status.RECUSADA, Status.RETORNO_PENDENTE)

    # Situacoes que exigem dizer POR QUE a cortesia nao chegou.
    EXIGEM_MOTIVO = (Status.RECUSADA, Status.CANCELADA)

    # ── De onde veio a cortesia ──────────────────────────────────────────
    item_carga = models.OneToOneField(
        'logistica.ItemCarga', on_delete=models.CASCADE,
        null=True, blank=True, related_name='entrega_bonificacao',
    )
    entrega_rua = models.OneToOneField(
        'logistica.VendaViagem', on_delete=models.CASCADE,
        null=True, blank=True, related_name='acompanhamento',
    )

    status = models.CharField(
        max_length=20, choices=Status.choices,
        default=Status.PENDENTE, db_index=True,
    )

    # ── A entrega ────────────────────────────────────────────────────────
    entregue_em = models.DateTimeField(null=True, blank=True)
    entregue_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='bonificacoes_entregues',
    )
    # A QUANTIDADE ENTREGUE PODE SER MENOR que a prometida: o cliente aceita
    # 15 das 20. Assumir que chegou tudo esconderia justamente a diferença
    # que precisa voltar.
    quantidade_entregue = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
    )
    destinatario_nome = models.CharField(
        max_length=120, blank=True,
        help_text='Quem recebeu — é ele que responde por ter recebido.',
    )
    destinatario_documento = models.CharField(max_length=30, blank=True)

    # ── Quando ela não chega ─────────────────────────────────────────────
    motivo_nao_entrega = models.CharField(
        max_length=20, choices=MotivoNaoEntrega.choices, blank=True,
        db_index=True,
        help_text='Por que a bonificação não foi entregue.',
    )
    nao_entregue_em = models.DateTimeField(null=True, blank=True)
    retorno_tratado_em = models.DateTimeField(
        null=True, blank=True,
        help_text='Quando a mercadoria voltou de fato ao estoque ou ao saldo.',
    )
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = 'logistica_entregas_bonificacao'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status'])]
        constraints = [
            # UMA ORIGEM, E EXATAMENTE UMA. Sem isso, uma entrega orfa ficaria
            # contada no relatorio sem ter o que a explique -- e uma com duas
            # origens apareceria duas vezes.
            models.CheckConstraint(
                condition=(
                    models.Q(item_carga__isnull=False, entrega_rua__isnull=True)
                    | models.Q(item_carga__isnull=True, entrega_rua__isnull=False)
                ),
                name='bonificacao_tem_uma_origem',
            ),
        ]
        verbose_name = 'Entrega de bonificação'
        verbose_name_plural = 'Entregas de bonificação'

    def __str__(self):
        return f'Bonificação {self.origem} — {self.get_status_display()}'

    def clean(self):
        if bool(self.item_carga_id) == bool(self.entrega_rua_id):
            raise ValidationError(
                'A entrega de bonificação precisa de uma origem: a linha da '
                'carga ou a entrega feita na rua.'
            )

    @property
    def origem(self):
        return self.item_carga or self.entrega_rua

    @property
    def viagem(self):
        origem = self.origem
        return getattr(origem, 'viagem', None)

    @property
    def aberta(self) -> bool:
        return self.status in self.ABERTAS

    @property
    def entregue(self) -> bool:
        return self.status == self.Status.ENTREGUE

    @property
    def nao_entregue(self) -> bool:
        """
        Mercadoria que saiu, não virou entrega e ainda não voltou.

        É este o estado que precisa ficar identificado: a caixa está no
        caminhão, fora do estoque e sem dono. Enquanto ele durar, a
        bonificação aparece marcada como NÃO ENTREGUE.
        """
        return self.status in self.NAO_ENTREGUES

    @property
    def rotulo_nao_entregue(self) -> str:
        """O que a etiqueta diz — vazio quando não é o caso."""
        if not self.nao_entregue:
            return ''
        motivo = self.get_motivo_nao_entrega_display() if self.motivo_nao_entrega else ''
        return f'BONIFICAÇÃO NÃO ENTREGUE — {motivo}' if motivo else (
            'BONIFICAÇÃO NÃO ENTREGUE'
        )

    @property
    def tem_prova(self) -> bool:
        """
        Se existe algo além da palavra de quem marcou.

        "Entregue: sim" não prova nada — e a bonificação é justamente a
        entrega que ninguém reclama quando não chega.
        """
        return self.comprovantes.exists()

    def pode_ir_para(self, destino: str) -> bool:
        return destino in self.TRANSICOES.get(self.status, ())

    @property
    def proximos(self) -> list[tuple[str, str]]:
        rotulos = dict(self.Status.choices)
        return [(s, rotulos[s]) for s in self.TRANSICOES.get(self.status, ())]


class ComprovanteBonificacao(TimestampedModel):
    """A prova de que a cortesia chegou."""

    class Tipo(models.TextChoices):
        FOTO = 'foto', 'Foto'
        ASSINATURA = 'assinatura', 'Assinatura'
        COMPROVANTE = 'comprovante', 'Comprovante'
        CANHOTO = 'canhoto', 'Canhoto'

    entrega = models.ForeignKey(
        EntregaBonificacao, on_delete=models.CASCADE,
        related_name='comprovantes',
    )
    tipo = models.CharField(max_length=15, choices=Tipo.choices, db_index=True)
    arquivo = models.FileField(upload_to=caminho_do_comprovante)
    descricao = models.CharField(max_length=160, blank=True)
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='comprovantes_bonificacao',
    )
    enviado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'logistica_comprovantes_bonificacao'
        ordering = ['enviado_em']
        indexes = [models.Index(fields=['entrega', 'tipo'])]
        verbose_name = 'Comprovante de bonificação'
        verbose_name_plural = 'Comprovantes de bonificação'

    def __str__(self):
        return f'{self.get_tipo_display()} — {self.entrega}'
