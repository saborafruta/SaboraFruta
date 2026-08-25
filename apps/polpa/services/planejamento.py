"""
O PPCP: o que produzir, quanto, quando e em qual recurso.

A CONTA DA NECESSIDADE, escrita uma vez:

    necessidade = pedidos em aberto + estoque mínimo + previsão de vendas
                  − estoque disponível − o que já está em produção

O ÚLTIMO TERMO É O QUE FALTA EM QUASE TODO SISTEMA. Sem descontar as ordens
abertas, a sugestão manda produzir de novo o que já está na linha — e é
assim que se produz o dobro do que se vende, com fruta comprada a mais e
câmara cheia de produto vencendo.

A PREVISÃO É HISTÓRICO, não profecia. Média diária do que saiu nos últimos
90 dias, projetada no horizonte pedido. Não é um modelo estatístico e não
finge ser: uma previsão elaborada que ninguém entende é pior que uma média
simples que todo mundo confere. Onde não há histórico, a previsão é ZERO e a
tela diz "sem histórico" -- inventar demanda para produto novo é como se
enche a câmara de item que ninguém pediu.

O PLANEJAMENTO NÃO CRIA ORDEM SOZINHO. Ele sugere; alguém decide. Geração
automática de OP parece eficiência até a primeira semana em que a fábrica
produz por uma sugestão errada e ninguém consegue explicar quem mandou.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import F, Sum
from django.utils import timezone

from apps.estoque.models import Estoque
from apps.polpa.models import FichaProduto, OrdemPolpa, Receita, Recurso
from apps.producao.models import FichaTecnica
from apps.vendas.models import ItemPedidoVenda, PedidoVenda

ZERO = Decimal('0')

# Pedido que ainda vai sair da fábrica. Faturado e entregue já saíram;
# rascunho e orçamento ainda não são compromisso -- contar os dois faria a
# sugestão produzir para venda que não existe.
PEDIDOS_EM_ABERTO = (
    PedidoVenda.Status.APROVADO,
    PedidoVenda.Status.CONFIRMADO,
    PedidoVenda.Status.EM_SEPARACAO,
    PedidoVenda.Status.PARCIALMENTE_FATURADO,
)

# O que já saiu de verdade, e por isso serve de base para a previsão.
PEDIDOS_ATENDIDOS = (
    PedidoVenda.Status.FATURADO,
    PedidoVenda.Status.PARCIALMENTE_FATURADO,
    PedidoVenda.Status.ENTREGUE,
)

DIAS_DE_HISTORICO = 90


class PlanejamentoService:

    # ── Sugestão ─────────────────────────────────────────────────────────

    @classmethod
    def sugestoes(cls, filial, horizonte: int = 30) -> list[dict]:
        """
        O que produzir nos próximos `horizonte` dias, produto a produto.

        Devolve TODOS os acabados, e não só os que precisam: ver que um
        produto está coberto é informação — some da lista e alguém vai
        perguntar se ele foi esquecido.
        """
        fichas = (
            FichaProduto.objects.for_filial(filial)
            .filter(classe=FichaProduto.Classe.ACABADO)
            .select_related('produto', 'produto__unidade_medida')
        )

        pedidos = cls._pedidos_em_aberto(filial)
        estoques = cls._estoques(filial)
        producao = cls._em_producao(filial)
        media = cls._media_diaria(filial)
        receitas = cls._receitas_ativas(filial)

        linhas = []
        for ficha in fichas:
            produto = ficha.produto
            em_aberto = pedidos.get(produto.pk, ZERO)
            estoque = estoques.get(produto.pk, ZERO)
            minimo = produto.estoque_minimo or ZERO
            diaria = media.get(produto.pk, ZERO)
            previsao = (diaria * horizonte).quantize(Decimal('0.001'))
            aberto_producao = producao.get(produto.pk, ZERO)

            necessidade = em_aberto + minimo + previsao - estoque - aberto_producao
            linhas.append({
                'produto': produto,
                'unidade': getattr(produto.unidade_medida, 'sigla', ''),
                'pedidos': em_aberto,
                'estoque': estoque,
                'minimo': minimo,
                'previsao': previsao,
                'media_diaria': diaria,
                'em_producao': aberto_producao,
                'necessidade': max(necessidade, ZERO),
                'sem_historico': diaria <= ZERO,
                'receita': receitas.get(produto.pk),
            })

        # O que precisa primeiro vem primeiro; empate desempata pelo nome,
        # para a lista não dançar a cada abertura.
        linhas.sort(key=lambda l: (-l['necessidade'], l['produto'].descricao))
        return linhas

    @staticmethod
    def _pedidos_em_aberto(filial) -> dict:
        """
        O que os clientes já pediram e ainda não recebeu.

        `quantidade - quantidade_atendida`, e não a quantidade cheia: o que
        já foi faturado saiu do estoque e não precisa ser produzido de novo.
        """
        linhas = (
            ItemPedidoVenda.objects
            .filter(pedido__filial=filial, pedido__status__in=PEDIDOS_EM_ABERTO)
            .values('produto_id')
            .annotate(falta=Sum(F('quantidade') - F('quantidade_atendida')))
        )
        return {
            l['produto_id']: max(l['falta'] or ZERO, ZERO) for l in linhas
        }

    @staticmethod
    def _estoques(filial) -> dict:
        return {
            e.produto_id: e.quantidade_disponivel or ZERO
            for e in Estoque.objects.filter(filial=filial)
        }

    @staticmethod
    def _em_producao(filial) -> dict:
        """
        O que já está na linha — o termo que evita produzir em dobro.

        Conta o PLANEJADO menos o já produzido da ordem: uma OP com metade
        feita ainda vai entregar a outra metade.
        """
        saldo: dict = {}
        for op in (
            OrdemPolpa.objects.for_filial(filial)
            .filter(situacao__in=OrdemPolpa.ABERTAS)
            .select_related('ordem')
        ):
            falta = (op.ordem.quantidade_planejada or ZERO) - (
                op.ordem.quantidade_produzida or ZERO
            )
            if falta > ZERO:
                chave = op.ordem.produto_acabado_id
                saldo[chave] = saldo.get(chave, ZERO) + falta
        return saldo

    @staticmethod
    def _media_diaria(filial, dias: int = DIAS_DE_HISTORICO) -> dict:
        """
        Quanto sai por dia, pelo histórico — a previsão desta casa.

        Média simples, de propósito: um modelo elaborado que ninguém entende
        é pior que uma conta que qualquer pessoa confere na calculadora.
        """
        desde = timezone.now() - timedelta(days=dias)
        linhas = (
            ItemPedidoVenda.objects
            .filter(
                pedido__filial=filial,
                pedido__status__in=PEDIDOS_ATENDIDOS,
                pedido__data_emissao__gte=desde,
            )
            .values('produto_id')
            .annotate(total=Sum('quantidade'))
        )
        return {
            l['produto_id']: ((l['total'] or ZERO) / dias).quantize(Decimal('0.001'))
            for l in linhas
        }

    @staticmethod
    def _receitas_ativas(filial) -> dict:
        """A receita ativa de cada produto — sem ela não dá para abrir OP."""
        return {
            r.ficha.produto_acabado_id: r
            for r in (
                Receita.objects.for_filial(filial)
                .filter(ficha__status=FichaTecnica.Status.ATIVA)
                .select_related('ficha')
            )
        }

    # ── Calendário ───────────────────────────────────────────────────────

    @classmethod
    def calendario(cls, filial, inicio: date, fim: date) -> list[dict]:
        """
        As ordens programadas dia a dia, com a carga de cada dia.

        A DATA QUE VALE É A DE INÍCIO PREVISTO. Ordem sem data programada
        não some do sistema -- ela aparece numa lista à parte ("sem data"),
        porque produção sem dia marcado é o que ninguém lembra de fazer.
        """
        ordens = (
            OrdemPolpa.objects.for_filial(filial)
            .exclude(situacao=OrdemPolpa.Situacao.CANCELADA)
            .select_related('ordem', 'ordem__produto_acabado', 'recurso')
        )

        por_dia: dict = {}
        for op in ordens:
            previsto = op.ordem.data_inicio_prevista
            if not previsto:
                continue
            dia = timezone.localtime(previsto).date()
            if inicio <= dia <= fim:
                por_dia.setdefault(dia, []).append(op)

        dias = []
        atual = inicio
        while atual <= fim:
            do_dia = por_dia.get(atual, [])
            dias.append({
                'dia': atual,
                'ordens': do_dia,
                'carga': sum(
                    (o.ordem.quantidade_planejada or ZERO for o in do_dia), ZERO,
                ),
                'hoje': atual == timezone.localdate(),
            })
            atual += timedelta(days=1)
        return dias

    @classmethod
    def sem_data(cls, filial):
        """Ordens em aberto que ninguém programou — as que se perdem."""
        return (
            OrdemPolpa.objects.for_filial(filial)
            .filter(
                situacao__in=OrdemPolpa.ABERTAS,
                ordem__data_inicio_prevista__isnull=True,
            )
            .select_related('ordem', 'ordem__produto_acabado')
        )

    # ── Kanban ───────────────────────────────────────────────────────────

    @classmethod
    def kanban(cls, filial) -> list[dict]:
        """
        As ordens em aberto por situação — o quadro da fábrica.

        CANCELADA NÃO É COLUNA: não é um lugar do fluxo, é a saída dele, e
        ficaria acumulando cartões mortos no fim do quadro. Produzida entra,
        porque é o fim do caminho e quem olha o quadro quer ver o que saiu
        hoje.
        """
        ordens = list(
            OrdemPolpa.objects.for_filial(filial)
            .exclude(situacao=OrdemPolpa.Situacao.CANCELADA)
            .select_related('ordem', 'ordem__produto_acabado', 'recurso')
            .order_by('ordem__data_inicio_prevista', 'ordem__numero')
        )
        colunas = []
        for valor, rotulo in OrdemPolpa.Situacao.choices:
            if valor == OrdemPolpa.Situacao.CANCELADA:
                continue
            do_grupo = [o for o in ordens if o.situacao == valor]
            colunas.append({
                'chave': valor,
                'label': rotulo,
                'ordens': do_grupo,
                'total': len(do_grupo),
                'quantidade': sum(
                    (o.ordem.quantidade_planejada or ZERO for o in do_grupo), ZERO,
                ),
            })
        return colunas

    # ── Capacidade ───────────────────────────────────────────────────────

    @classmethod
    def carga_por_recurso(cls, filial, inicio: date, fim: date) -> list[dict]:
        """
        Quanto foi programado em cada recurso no período, contra o que ele
        aguenta.

        SEM CAPACIDADE CADASTRADA a ocupação é `None`, e a tela diz isso: 0%
        seria lido como recurso livre, e 100% como lotado — as duas leituras
        erradas levam a decisão errada.
        """
        dias_uteis = max((fim - inicio).days + 1, 1)
        recursos = Recurso.objects.for_filial(filial).filter(ativo=True)

        ordens = (
            OrdemPolpa.objects.for_filial(filial)
            .filter(
                situacao__in=OrdemPolpa.ABERTAS,
                ordem__data_inicio_prevista__date__gte=inicio,
                ordem__data_inicio_prevista__date__lte=fim,
            )
            .select_related('ordem')
        )

        carga: dict = {}
        sem_recurso = ZERO
        for op in ordens:
            quantidade = op.ordem.quantidade_planejada or ZERO
            if op.recurso_id:
                carga[op.recurso_id] = carga.get(op.recurso_id, ZERO) + quantidade
            else:
                sem_recurso += quantidade

        linhas = []
        for recurso in recursos:
            programado = carga.get(recurso.pk, ZERO)
            disponivel = (
                recurso.capacidade_dia * dias_uteis if recurso.capacidade_dia else None
            )
            linhas.append({
                'recurso': recurso,
                'programado': programado,
                'disponivel': disponivel,
                'ocupacao': (
                    (programado / disponivel * 100).quantize(Decimal('0.1'))
                    if disponivel else None
                ),
                'estourou': bool(disponivel and programado > disponivel),
            })
        return [{'linhas': linhas, 'sem_recurso': sem_recurso, 'dias': dias_uteis}]

    # ── Programação ──────────────────────────────────────────────────────

    @staticmethod
    def programar(op: OrdemPolpa, quando, recurso: Recurso | None = None) -> OrdemPolpa:
        """
        Marca o dia e o recurso da ordem.

        PROGRAMAR NÃO LIBERA. São decisões diferentes: programar diz quando
        se pretende fazer, liberar diz que pode começar. Juntar as duas faria
        toda ordem colocada no calendário virar autorização de consumo.
        """
        from apps.core.services.exceptions import DomainError

        if op.encerrada:
            raise DomainError('Ordem encerrada não entra na programação.')

        op.ordem.data_inicio_prevista = quando
        op.ordem.save(update_fields=['data_inicio_prevista', 'updated_at'])
        if recurso is not None:
            op.recurso = recurso
            op.save(update_fields=['recurso', 'updated_at'])
        return op
