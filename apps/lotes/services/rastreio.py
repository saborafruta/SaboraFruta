"""
Rastreabilidade de lote, nas duas direções — o caminho do recall.

AS DUAS PERGUNTAS que este módulo existe para responder:

    "De onde veio este produto?"
    acabado → OP → lotes de MP → fornecedor/produtor → nota → recebimento

    "Onde foi parar esta matéria-prima?"
    MP → OPs que a consumiram → acabados → clientes/pedidos

O QUE FALTAVA NÃO ERA A TELA, ERA A TRAVESSIA. A tela mostrava, como
"componentes consumidos", os itens da FICHA TÉCNICA — a receita. Está
honestamente rotulada, e serve para conferir a formulação; mas num recall ela
não responde nada. A receita diz "manga"; o recall precisa de "lote L-4471, do
produtor Silva, recebido em 12/08 com 8% de impureza". São perguntas
diferentes, e a segunda é a única que faz o telefone tocar.

O DADO SEMPRE ESTEVE LÁ. Quando a OP encerra, cada baixa de MP grava uma
`MovimentacaoEstoque` com o LOTE escolhido pelo FEFO, o documento
`ordem_producao` e o id da OP; e o lote do acabado nasce com
`ordem_producao_id`. Os dois ponteiros fecham o circuito nos dois sentidos —
ninguém tinha ligado um no outro.

RECURSIVO, PORQUE A CADEIA TEM DEGRAUS. Base de açaí vira sorvete; polpa vira
mix. Parar no primeiro nível responderia "veio da base X" e esconderia a fruta
que de fato está sob suspeita — que é a única coisa que importa quando o
problema é da matéria-prima.

`PROFUNDIDADE` e o conjunto de visitados existem por isso: cadeia com laço
(um lote que, por erro de cadastro, aponta para si mesmo) travaria a tela em
vez de mostrar o que já se sabe.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from apps.estoque.models import LoteProduto, MovimentacaoEstoque

ZERO = Decimal('0')

# Cinco degraus cobrem fruta → base → mix → acabado com folga. Mais que isso,
# numa fábrica de alimento, é sinal de cadastro errado — e uma tela que desce
# vinte níveis não é mais legível que uma que para e diz que parou.
PROFUNDIDADE = 5

DOC_OP = MovimentacaoEstoque.DocumentoTipo.ORDEM_PRODUCAO
SAIDA_PRODUCAO = MovimentacaoEstoque.TipoOperacao.PRODUCAO_SAIDA


@dataclass
class Elo:
    """
    Um lote no caminho, e COMO se chegou até ele.

    Lista achatada com `nivel` em vez de árvore aninhada: a tela desenha
    indentação a partir do nível, e uma árvore aninhada em template Django
    exige recursão por include — que é mais código para o mesmo desenho.
    """
    lote: object
    nivel: int
    via: str = ''            # producao | compra | recebimento | venda
    quantidade: Decimal | None = None
    ordem: object = None     # producao.OrdemProducao, quando via=producao
    entrada: object = None   # compras.ItemEntradaNF, quando via=compra
    recebimento: object = None
    separacao: object = None  # vendas.ItemSeparacao, quando via=venda

    @property
    def fornecedor(self):
        """Quem entregou este lote, seja qual for a porta de entrada."""
        if self.recebimento is not None:
            return getattr(self.recebimento, 'produtor', None)
        if self.entrada is not None:
            return getattr(getattr(self.entrada, 'entrada', None), 'fornecedor', None)
        return getattr(self.lote, 'fornecedor', None)

    @property
    def documento(self) -> str:
        """A nota que trouxe o lote, quando existe."""
        if self.recebimento is not None:
            return getattr(self.recebimento, 'nota_fiscal', '') or ''
        if self.entrada is not None:
            entrada = getattr(self.entrada, 'entrada', None)
            return getattr(entrada, 'documento_numero', '') or ''
        return getattr(self.lote, 'numero_nota_entrada', '') or ''


class RastreioService:

    # ── "De onde veio este produto?" ─────────────────────────────────────

    @classmethod
    def de_onde_veio(cls, lote, profundidade: int = PROFUNDIDADE) -> list[Elo]:
        """
        O caminho para trás: os lotes que entraram neste, e o que entrou neles.

        O primeiro elo é o próprio lote, no nível 0 — sem ele a lista começa
        no meio da história, e quem abre a tela não sabe de onde partiu.
        """
        elos: list[Elo] = []
        visitados: set[int] = set()

        def descer(atual, nivel: int, via: str, quantidade):
            if nivel > profundidade or atual.pk in visitados:
                return
            visitados.add(atual.pk)

            elo = Elo(lote=atual, nivel=nivel, via=via, quantidade=quantidade)
            elo.entrada = cls._compra_do_lote(atual)
            elo.recebimento = cls._recebimento_do_lote(atual)
            ordem = cls._ordem_que_produziu(atual)
            elo.ordem = ordem
            elos.append(elo)

            if ordem is None:
                return
            for lote_mp, consumido in cls._consumidos_pela_ordem(ordem):
                descer(lote_mp, nivel + 1, 'producao', consumido)

        descer(lote, 0, '', None)
        return elos

    # ── "Onde foi parar esta matéria-prima?" ─────────────────────────────

    @classmethod
    def para_onde_foi(cls, lote, profundidade: int = PROFUNDIDADE) -> list[Elo]:
        """
        O caminho para a frente: as OPs que comeram este lote, o que elas
        produziram, e para quem foi.

        A VENDA É FOLHA, e não continua a descida: o produto saiu da empresa.
        Quem recebeu está no elo, e é onde o recall liga.
        """
        elos: list[Elo] = []
        visitados: set[int] = set()

        def subir(atual, nivel: int, via: str, quantidade):
            if nivel > profundidade or atual.pk in visitados:
                return
            visitados.add(atual.pk)
            elos.append(Elo(lote=atual, nivel=nivel, via=via, quantidade=quantidade))

            for item in cls._vendas_do_lote(atual):
                elos.append(Elo(
                    lote=atual, nivel=nivel + 1, via='venda',
                    quantidade=getattr(item, 'quantidade_separada', None),
                    separacao=item,
                ))

            for acabado, produzido in cls._acabados_que_usaram(atual):
                subir(acabado, nivel + 1, 'producao', produzido)

        subir(lote, 0, '', None)
        return elos

    # ── O que o recall precisa em uma linha ──────────────────────────────

    @staticmethod
    def resumo(origem: list[Elo], destino: list[Elo]) -> dict:
        """
        As pontas da corrente, sem os degraus do meio.

        É o que se lê primeiro quando o telefone toca: de quem veio, e para
        quem foi. O caminho completo continua abaixo, para quem precisar
        explicar depois.
        """
        fornecedores = []
        vistos = set()
        for elo in origem:
            quem = elo.fornecedor
            if quem is not None and quem.pk not in vistos:
                vistos.add(quem.pk)
                fornecedores.append(quem)

        clientes = {}
        for elo in destino:
            if elo.separacao is None:
                continue
            pedido = getattr(getattr(elo.separacao, 'separacao', None), 'pedido', None)
            cliente = getattr(pedido, 'cliente', None)
            if cliente is None:
                continue
            registro = clientes.setdefault(cliente.pk, {
                'cliente': cliente, 'pedidos': [], 'quantidade_total': ZERO,
            })
            if pedido not in registro['pedidos']:
                registro['pedidos'].append(pedido)
            registro['quantidade_total'] += elo.quantidade or ZERO

        return {
            'fornecedores': fornecedores,
            'clientes': list(clientes.values()),
            'lotes_origem': sum(1 for e in origem if e.nivel > 0),
            'lotes_destino': sum(
                1 for e in destino if e.nivel > 0 and e.via == 'producao'
            ),
        }

    # ── Os elos, um a um ─────────────────────────────────────────────────

    @staticmethod
    def _ordem_que_produziu(lote):
        """A OP que gerou este lote, pelos dois ponteiros que existem."""
        from apps.producao.models import OrdemProducao

        if lote.ordem_producao_id:
            return (
                OrdemProducao.objects
                .select_related('produto_acabado', 'ficha_tecnica')
                .filter(pk=lote.ordem_producao_id)
                .first()
            )
        # `lote_gerado` é o caminho antigo, e continua valendo para as OPs
        # encerradas antes de `ordem_producao_id` existir.
        return (
            lote.ordens_origem
            .select_related('produto_acabado', 'ficha_tecnica')
            .order_by('-pk')
            .first()
        )

    @staticmethod
    def _consumidos_pela_ordem(ordem) -> list[tuple]:
        """
        Os lotes de MP que esta OP realmente comeu, e quanto de cada um.

        Vem do RAZÃO, não da ficha: a ficha diz o que deveria entrar, o razão
        diz o que entrou. Num recall é a segunda coisa que importa.
        """
        movimentos = (
            MovimentacaoEstoque.objects
            .filter(
                documento_tipo=DOC_OP,
                documento_id=ordem.pk,
                tipo_operacao=SAIDA_PRODUCAO,
                lote__isnull=False,
            )
            .select_related('lote', 'lote__produto', 'lote__fornecedor')
            .order_by('pk')
        )
        somado: dict[int, list] = {}
        for movimento in movimentos:
            registro = somado.setdefault(
                movimento.lote_id, [movimento.lote, ZERO],
            )
            registro[1] += movimento.quantidade or ZERO
        return [(lote, quantidade) for lote, quantidade in somado.values()]

    @staticmethod
    def _acabados_que_usaram(lote) -> list[tuple]:
        """
        Os lotes de produto acabado que saíram das OPs que comeram este lote.

        Duas consultas em vez de um join porque o razão guarda o id da OP num
        inteiro solto (`documento_id`), e não numa chave estrangeira — não há
        join para fazer.
        """
        ordens = set(
            MovimentacaoEstoque.objects
            .filter(
                lote=lote,
                documento_tipo=DOC_OP,
                tipo_operacao=SAIDA_PRODUCAO,
                documento_id__isnull=False,
            )
            .values_list('documento_id', flat=True)
        )
        if not ordens:
            return []

        acabados = (
            LoteProduto.objects
            .filter(ordem_producao_id__in=ordens)
            .select_related('produto')
            .order_by('pk')
        )
        return [(acabado, acabado.quantidade_inicial) for acabado in acabados]

    @staticmethod
    def _compra_do_lote(lote):
        from apps.compras.models import ItemEntradaNF

        return (
            ItemEntradaNF.objects
            .filter(lote_gerado=lote)
            .select_related('entrada', 'entrada__fornecedor', 'produto')
            .order_by('-entrada__data_entrada', '-pk')
            .first()
        )

    @staticmethod
    def _recebimento_do_lote(lote):
        """
        O romaneio da balança, quando o vertical de polpa está instalado.

        Import local e tolerante: `lotes` é do núcleo e roda em empresa que
        não tem o vertical de fruta. Falhar aqui apagaria a rastreabilidade
        inteira por causa de um app ausente.
        """
        try:
            from apps.polpa.models import Recebimento
        except Exception:  # noqa: BLE001 — vertical ausente é caso normal
            return None
        return (
            Recebimento.all_objects
            .filter(lote=lote)
            .select_related('produtor')
            .order_by('-pk')
            .first()
        )

    @staticmethod
    def _vendas_do_lote(lote) -> list:
        from apps.vendas.models import ItemSeparacao

        return list(
            ItemSeparacao.objects
            .filter(lote=lote)
            .select_related(
                'separacao', 'separacao__pedido', 'separacao__pedido__cliente',
            )
            .order_by('-separacao__data_inicio')
        )
