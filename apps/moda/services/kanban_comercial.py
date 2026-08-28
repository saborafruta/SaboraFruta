"""
Kanban comercial — o pedido como cartão, do orçamento à entrega.

É O MESMO PEDIDO, VISTO POR OUTRO ÂNGULO. O quadro não tem tabela própria e
não guarda "coluna": a coluna é derivada do `status` do `PedidoProducao`,
que é o campo que o vertical inteiro já lê. Uma coluna gravada seria uma
segunda verdade sobre onde o pedido está, e na primeira liberação feita pela
tela do pedido as duas divergiriam.

ARRASTAR É MUDAR O STATUS DO PEDIDO:

  · soltar em Produção RODA as onze validações (`ValidacaoProducao`) e deixa
    passar, devolvendo o que falta como aviso. O cartão diz onde o pedido
    está; barrar o arrasto não fazia a ficha aparecer, só impedia o quadro
    de contar a verdade sobre um pedido que já estava indo para a fábrica.
    A cancela de verdade ficou onde o estrago existe: emitir a ordem, que é
    o ato que corta tecido;
  · soltar em Pedido Confirmado o que ainda é orçamento passa pelo
    `OrcamentoService.fechar`, que é quem sabe o que falta para uma proposta
    virar compromisso — ali a recusa continua, porque fechar orçamento não é
    registrar onde o pedido está, é assumir um compromisso de preço.

Uma coluna pode abrigar MAIS DE UM STATUS. "Produção" recolhe liberado, em
produção e em acabamento porque, para o comercial, os três respondem a mesma
pergunta ("está na fábrica"); quem precisa do detalhe abre o kanban de
produção, que trabalha por etapa. Ao soltar um cartão ali, o status que vale
é o primeiro do grupo — o avanço para "Em Produção" vem do apontamento de
quem produz, não do arrasto de quem vende.

CANCELADO NÃO É COLUNA. Não é um lugar do fluxo, é a saída dele; ficaria
acumulando cartões mortos no fim do quadro. O total aparece no cabeçalho,
para não sumir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.moda.models import PedidoProducao
from apps.moda.services.financeiro import FinanceiroPedidoService

S = PedidoProducao.Status
ZERO = Decimal('0')


@dataclass(frozen=True)
class Coluna:
    chave: str
    label: str
    # Status que caem nesta coluna.
    status: tuple[str, ...]
    # Status que passa a valer quando um cartão é solto aqui.
    destino: str
    descricao: str = ''


COLUNAS: list[Coluna] = [
    Coluna('orcamento', 'Orçamento', (S.ORCAMENTO,), S.ORCAMENTO,
           'Proposta ainda sem o sim do cliente.'),
    Coluna('aprovacao', 'Aguardando Aprovação', (S.AGUARDANDO_APROVACAO,),
           S.AGUARDANDO_APROVACAO, 'Arte enviada; a bola está com o cliente.'),
    Coluna('confirmado', 'Pedido aprovado', (S.CONFIRMADO, S.AGUARDANDO_ARTE), S.CONFIRMADO,
           'Cliente aceitou. Vai para o PCP.'),
    Coluna('producao', 'Produção',
           (S.AGUARDANDO_MATERIAL, S.LIBERADO_PRODUCAO, S.EM_PRODUCAO, S.EM_ACABAMENTO),
           S.LIBERADO_PRODUCAO, 'Na fábrica: liberado, produzindo ou acabando.'),
    Coluna('pronto', 'Pronto para retirada', (S.PRONTO,), S.PRONTO,
           'Peça acabada, esperando sair.'),
    Coluna('entregue', 'Entregue', (S.ENTREGUE,), S.ENTREGUE,
           'Chegou ao cliente. Fim do fluxo.'),
]

COLUNAS_POR_CHAVE = {c.chave: c for c in COLUNAS}
COLUNA_DO_STATUS = {s: c.chave for c in COLUNAS for s in c.status}


def status_choices_kanban() -> list[tuple[str, str]]:
    """Destinos exibidos no detalhe do pedido, iguais às colunas do Kanban."""

    return [(coluna.destino, coluna.label) for coluna in COLUNAS]


def status_destino_kanban(status_atual: str) -> str:
    """Representa estados internos pela coluna visível correspondente."""

    chave = COLUNA_DO_STATUS.get(status_atual)
    return COLUNAS_POR_CHAVE[chave].destino if chave else ''

# Os status que colocam o pedido na mão da fábrica. Mesma lista da tela do
# pedido: chegar em qualquer um deles cobra as onze validações.
LIBERAM_PRODUCAO = (S.LIBERADO_PRODUCAO, S.EM_PRODUCAO)


@dataclass
class Cartao:
    pedido: PedidoProducao
    coluna: str
    # Dias até a entrega. Negativo é atraso; None é pedido sem data marcada,
    # que não é o mesmo que "no prazo" e por isso não vira zero.
    dias: int | None
    tem_arte: bool
    resposta_cliente: str = ''
    status_pagamento: dict = field(default_factory=dict)

    @property
    def atrasado(self) -> bool:
        return self.dias is not None and self.dias < 0

    @property
    def apertado(self) -> bool:
        return self.dias is not None and 0 <= self.dias <= 3

    @property
    def pediu_ajuste(self) -> bool:
        return self.resposta_cliente == 'ajuste'


@dataclass
class Raia:
    coluna: Coluna
    cartoes: list[Cartao] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.cartoes)

    @property
    def pecas(self) -> int:
        return sum(c.pedido.quantidade_total for c in self.cartoes)

    @property
    def valor(self) -> Decimal:
        return sum((c.pedido.valor_total for c in self.cartoes), ZERO)

    @property
    def atrasados(self) -> int:
        return sum(1 for c in self.cartoes if c.atrasado)


class KanbanComercialService:

    # ── Montagem do quadro ───────────────────────────────────────────────

    @staticmethod
    def base(filial):
        return (
            PedidoProducao.objects.for_filial(filial)
            .select_related('cliente', 'vendedor', 'aprovacao')
            .prefetch_related('itens__produto', 'itens__personalizacoes')
        )

    @classmethod
    def quadro(cls, filial, filtros: dict | None = None, hoje=None) -> dict:
        filtros = filtros or {}
        hoje = hoje or timezone.localdate()

        pedidos = list(cls._filtrar(cls.base(filial), filtros))
        situacoes = FinanceiroPedidoService.situacoes_dos_pedidos(
            pedidos, filial=filial,
        )
        raias = {c.chave: Raia(coluna=c) for c in COLUNAS}
        cancelados = 0

        for pedido in pedidos:
            if pedido.status == S.CANCELADO:
                cancelados += 1
                continue

            chave = COLUNA_DO_STATUS.get(pedido.status)
            if chave is None:
                # Status novo que ninguém mapeou. Cai no começo do fluxo em
                # vez de sumir: cartão invisível é pedido esquecido.
                chave = COLUNAS[0].chave

            aprovacao = getattr(pedido, 'aprovacao', None)
            raias[chave].cartoes.append(Cartao(
                pedido=pedido,
                coluna=chave,
                dias=(
                    (pedido.data_prevista_entrega - hoje).days
                    if pedido.data_prevista_entrega else None
                ),
                tem_arte=any(
                    i.personalizacoes.all() for i in pedido.itens.all()
                ),
                resposta_cliente=getattr(aprovacao, 'resposta', '') or '',
                status_pagamento=situacoes.get(
                    pedido.pk, FinanceiroPedidoService.situacao_pagamento(),
                ),
            ))

        # Dentro da raia: o mais urgente em cima. Pedido sem data vai para o
        # fim — não é urgente, é indefinido, e misturar os dois faria a
        # ausência de prazo parecer folga.
        for raia in raias.values():
            raia.cartoes.sort(key=lambda c: (
                c.dias if c.dias is not None else 10_000,
                -c.pedido.numero,
            ))

        return {
            'raias': [raias[c.chave] for c in COLUNAS],
            'cancelados': cancelados,
            'total': sum(r.total for r in raias.values()),
            'pecas': sum(r.pecas for r in raias.values()),
            'valor': sum((r.valor for r in raias.values()), ZERO),
            'atrasados': sum(r.atrasados for r in raias.values()),
        }

    @staticmethod
    def _filtrar(qs, filtros: dict):
        if filtros.get('busca'):
            termo = filtros['busca']
            qs = qs.filter(
                Q(numero__icontains=termo)
                | Q(cliente__razao_social__icontains=termo)
                | Q(cliente__nome_fantasia__icontains=termo)
                | Q(contato_nome__icontains=termo)
            )
        if filtros.get('prioridade'):
            qs = qs.filter(prioridade=filtros['prioridade'])
        if filtros.get('vendedor'):
            qs = qs.filter(vendedor_id=filtros['vendedor'])
        if filtros.get('atrasados'):
            qs = qs.filter(
                data_prevista_entrega__lt=timezone.localdate(),
            ).exclude(status__in=PedidoProducao.STATUS_ENCERRADOS)
        return qs

    # ── Movimentação ─────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def mover(cls, pedido: PedidoProducao, chave_coluna: str, usuario) -> dict:
        """
        Leva o pedido para a coluna indicada e devolve o que mudou.

        As travas são as mesmas do caminho normal, de propósito: um quadro
        que muda status sem cobrar o que a tela do pedido cobra vira a porta
        dos fundos por onde se libera produção sem ficha, sem grade e sem
        aprovação do cliente.
        """
        if not usuario or not usuario.tem_permissao('moda', 'editar'):
            raise DomainError('Seu perfil não pode movimentar pedidos no quadro.')

        coluna = COLUNAS_POR_CHAVE.get(chave_coluna)
        if coluna is None:
            raise DomainError('Coluna inválida.')

        if pedido.status == S.CANCELADO:
            raise DomainError(
                'Pedido cancelado não se movimenta. Reabra pela tela do pedido.'
            )

        if pedido.status in coluna.status:
            # Solto na própria coluna: nada mudou, e gravar mesmo assim
            # encheria a auditoria de eventos que não aconteceram.
            return {
                'coluna': chave_coluna,
                'status': pedido.status,
                'mudou': False,
                'avisos': [],
                'com_pendencia': False,
            }

        anterior = pedido.get_status_display()
        destino = coluna.destino
        avisos = []

        # Fechar orçamento tem regra própria — quem sabe o que falta para uma
        # proposta virar compromisso é o serviço de orçamento, e duplicar a
        # lista aqui faria as duas telas divergirem na primeira mudança.
        if pedido.status == S.ORCAMENTO and destino == S.CONFIRMADO:
            from apps.moda.services.orcamentos import OrcamentoService
            OrcamentoService.fechar(pedido, usuario)
        else:
            # PASSA, E DIZ O QUE FALTA. Antes isto era `exigir` e devolvia
            # erro: o cartão voltava para a coluna anterior e o quadro ficava
            # mostrando "Orçamento" para um pedido que a fábrica já ia fazer.
            # A pendência não some por causa da trava — ela some quando
            # alguém a lê, e para ler é preciso deixar a tela dizer.
            if destino in LIBERAM_PRODUCAO:
                from apps.moda.services.validacao import ValidacaoProducao
                pendencias = ValidacaoProducao.pendencias(pedido)
                if pendencias:
                    avisos.append(
                        f'Movido com {len(pendencias)} pendência(s) de produção: '
                        + ' '.join(pendencias)
                    )

            pedido.status = destino
            pedido.save(update_fields=['status', 'updated_at'])

        avisos.extend(cls._avisos(pedido, coluna))

        return {
            'coluna': chave_coluna,
            'status': pedido.status,
            'mudou': True,
            'anterior': anterior,
            'atual': pedido.get_status_display(),
            'avisos': avisos,
            # A tela pinta de âmbar em vez de verde: moveu, mas tem conta
            # para acertar. Sem essa marca o aviso passa como confirmação.
            'com_pendencia': bool(avisos),
        }

    @staticmethod
    def _avisos(pedido, coluna: Coluna) -> list[str]:
        """
        O que o movimento não impede, mas quem move precisa saber.

        Aviso não é validação: mandar para aprovação um pedido sem arte
        anexada pode ser exatamente o que o vendedor quis (a arte foi por
        WhatsApp). Bloquear seria arrogância; calar seria deixar o cliente
        receber um link vazio.
        """
        avisos = []

        if coluna.chave == 'aprovacao':
            tem_arte = any(
                i.personalizacoes.all() for i in pedido.itens.all()
            )
            if not tem_arte:
                avisos.append('Nenhuma arte anexada aos itens deste pedido.')

        if coluna.chave == 'entregue' and not pedido.financeiro_gerado_em:
            avisos.append('O financeiro deste pedido ainda não foi gerado.')

        return avisos


# ══════════════════════════════════════════════════════════════════════
# A RESPOSTA DO CLIENTE MOVE O CARTÃO
# ══════════════════════════════════════════════════════════════════════

# Até onde a resposta do cliente pode empurrar o pedido. Depois de
# `confirmado` o cartão está na mão da fábrica, e aceite que chega tarde não
# pode puxá-lo de volta para o comercial.
POSICAO = {c.chave: i for i, c in enumerate(COLUNAS)}


def posicao_do_pedido(pedido) -> int:
    """Onde o pedido está NO QUADRO — não na ordem do enum de status.

    As duas ordens são diferentes de propósito: no enum, `confirmado` vem
    antes de `aguardando_arte`, porque foi escrito na sequência do cadastro.
    No quadro ele vem DEPOIS da aprovação, porque é o cliente que confirma.
    Usar o enum aqui faria "avançar" andar para trás.
    """
    chave = COLUNA_DO_STATUS.get(pedido.status)
    return POSICAO.get(chave, -1)


def avancar_por_resposta(pedido, aprovacao) -> bool:
    """
    Move o cartão quando o cliente aceita. Devolve se mexeu.

    APROVOU: vai para "Pedido Confirmado" sozinho. Antes o cartão ficava em
    "Aguardando Aprovação" mesmo depois do sim, e alguém tinha de arrastá-lo
    à mão — trabalho que o sistema já sabia que era necessário, e que ficava
    esquecido justamente nos dias corridos.

    PEDIU AJUSTE: NÃO mexe. O pedido continua aguardando aprovação porque é
    isso que ele está fazendo — esperando a arte nova e o novo sim. Movê-lo
    para "Arte" pareceria mais organizado e apagaria o fato de haver um
    cliente esperando resposta; o quadro marca o cartão em vez de escondê-lo.

    SÓ AVANÇA, NUNCA VOLTA. Aceite que chega depois de a peça já estar na
    produção não puxa o cartão de volta para o comercial: a fábrica já
    passou por ali, e retroceder o status apagaria esse caminho.
    """
    if not aprovacao or not aprovacao.aprovado_pelo_cliente:
        return False
    if posicao_do_pedido(pedido) > POSICAO['aprovacao']:
        return False
    if pedido.status == S.CONFIRMADO:
        return False

    pedido.status = S.CONFIRMADO
    pedido.save(update_fields=['status', 'updated_at'])
    pedido.itens.update(status_fluxo='aprovado')
    return True
