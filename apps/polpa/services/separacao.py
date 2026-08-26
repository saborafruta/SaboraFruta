"""
A separação vista do chão da câmara: onde está o lote e quanto ele ainda dura.

POR QUE ISTO NÃO É UMA SEGUNDA SEPARAÇÃO. O ERP já sabe separar: o pedido
de venda tem `SeparacaoPedido`/`ItemSeparacao`, e `VendaService` escolhe os
lotes por FEFO e prende cada um ao item. Escrever outra aqui daria duas
separações do mesmo pedido -- e no faturamento alguém teria de escolher
qual delas valia.

O QUE FALTAVA ERA O CAMINHO ATÉ O PRODUTO. A separação do ERP responde
QUAL lote sai. Quem entra na câmara às 4h da manhã precisa de mais duas
coisas, e as duas só existem neste vertical:

  · ONDE aquele lote está -- câmara, corredor, rua, prateleira. Sem
    endereço, procura-se; e cada minuto de porta de câmara aberta é
    temperatura subindo em tudo que está lá dentro;

  · QUANTOS DIAS ele ainda tem. FEFO é uma regra, mas quem carrega precisa
    ver o número: um lote com quatro dias indo para um cliente que recebe
    semanal é uma devolução marcada.

A ORDEM FEFO NÃO É REESCRITA AQUI. Ela mora em
`MovimentacaoService.selecionar_lotes_fifo`, e este serviço a consulta --
inclusive para a sugestão da tela. Duas ordenações da mesma regra
consumiriam pelo lote errado no dia em que uma das duas mudasse.

A SUGESTÃO É PARCIAL, A SEPARAÇÃO É INTEIRA. Para MOSTRAR, o que os lotes
cobrem já é informação útil -- é assim que a tela consegue dizer "faltam
120 kg" em vez de recusar a página. Para SEPARAR de verdade, quem manda é
o `VendaService`, que recusa o que não tem saldo.
"""
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.estoque.models import LoteProduto
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.polpa.models import FichaProduto, LoteArmazenado
from apps.vendas.models import PedidoVenda
from apps.vendas.models.separacao import SeparacaoPedido

ZERO = Decimal('0')

# Pedido que ainda vai sair pela câmara. `CONFIRMADO` é o que o ERP aceita
# separar; `EM_SEPARACAO` continua na lista porque a carga só sai da tela
# quando é faturada -- some antes disso e quem está na doca acha que perdeu
# o pedido.
ABERTOS = (
    PedidoVenda.Status.CONFIRMADO,
    PedidoVenda.Status.EM_SEPARACAO,
)


class SeparacaoPolpaService:

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def _acabados(filial) -> set[int]:
        """Os produtos que este vertical fabrica — o resto não sai da câmara."""
        return set(
            FichaProduto.objects.for_filial(filial)
            .filter(classe=FichaProduto.Classe.ACABADO)
            .values_list('produto_id', flat=True)
        )

    @classmethod
    def pedidos(cls, filial, filtros: dict | None = None) -> list[dict]:
        """
        Os pedidos esperando a câmara, o mais urgente primeiro.

        ORDENADO PELA ENTREGA, não pela data do pedido: quem separa trabalha
        contra a data em que o caminhão sai. Pedido sem data de entrega vai
        para o fim -- e não some, porque some é como ele atrasa.
        """
        filtros = filtros or {}
        acabados = cls._acabados(filial)

        qs = (
            PedidoVenda.objects.filter(filial=filial, status__in=ABERTOS)
            .filter(itens__produto_id__in=acabados)
            .select_related('cliente')
            .prefetch_related('itens__produto')
            .distinct()
        )
        if filtros.get('busca'):
            from django.db.models import Q
            termo = filtros['busca']
            qs = qs.filter(
                Q(numero_pedido__icontains=termo)
                | Q(cliente__razao_social__icontains=termo)
                | Q(cliente__nome_fantasia__icontains=termo)
            )

        linhas = []
        for pedido in qs:
            itens = [i for i in pedido.itens.all() if i.produto_id in acabados]
            pendente = sum(
                max((i.quantidade or ZERO) - (i.quantidade_atendida or ZERO), ZERO)
                for i in itens
            )
            linhas.append({
                'pedido': pedido,
                'itens': len(itens),
                'pendente': pendente,
                'separado': pendente <= ZERO,
                'separacao': cls.separacao_atual(pedido),
                'atrasado': bool(
                    pedido.data_entrega_prevista
                    and pedido.data_entrega_prevista < timezone.localdate()
                ),
            })

        linhas.sort(key=lambda l: (
            l['pedido'].data_entrega_prevista is None,
            l['pedido'].data_entrega_prevista or timezone.localdate(),
            l['pedido'].numero_pedido,
        ))
        return linhas

    @staticmethod
    def separacao_atual(pedido) -> SeparacaoPedido | None:
        """A separação concluída mais recente — a que o faturamento vai usar."""
        return (
            pedido.separacoes
            .filter(status=SeparacaoPedido.Status.CONCLUIDA)
            .order_by('-data_inicio')
            .first()
        )

    @staticmethod
    def _enderecos(lote_ids) -> dict:
        """Onde cada lote está guardado, por id — uma consulta só."""
        armazenados = (
            LoteArmazenado.all_objects
            .filter(lote_id__in=list(lote_ids))
            .select_related('camara', 'posicao', 'lote')
        )
        return {a.lote_id: a for a in armazenados}

    @classmethod
    def mapa(cls, pedido) -> list[dict]:
        """
        A lista de separação: cada item com os lotes, o endereço e a validade.

        JÁ SEPARADO MOSTRA O QUE FOI, não o que seria. Depois que a
        separação existe, os lotes estão presos ao item -- reexibir a
        sugestão faria a tela discordar do documento que o faturamento vai
        ler.
        """
        acabados = cls._acabados(pedido.filial)
        separacao = cls.separacao_atual(pedido)
        hoje = timezone.localdate()

        linhas = []
        for item in pedido.itens.select_related(
            'produto', 'produto__unidade_medida',
        ).all():
            if item.produto_id not in acabados:
                continue

            if separacao:
                escolhidos = [
                    {'lote': i.lote, 'quantidade': i.quantidade_separada}
                    for i in separacao.itens.select_related('lote')
                    .filter(item_pedido=item)
                ]
                falta = ZERO
            else:
                escolhidos, falta = cls._sugestao(item, pedido.filial_id)

            enderecos = cls._enderecos(e['lote'].pk for e in escolhidos)
            for escolha in escolhidos:
                lote = escolha['lote']
                armazenado = enderecos.get(lote.pk)
                escolha['armazenado'] = armazenado
                escolha['onde'] = (
                    f'{armazenado.camara.nome} · {armazenado.onde}'
                    if armazenado and armazenado.onde
                    else (armazenado.camara.nome if armazenado else '')
                )
                escolha['dias'] = (
                    (lote.data_validade - hoje).days if lote.data_validade else None
                )

            linhas.append({
                'item': item,
                'produto': item.produto,
                'quantidade': item.quantidade,
                'atendida': item.quantidade_atendida,
                'escolhidos': escolhidos,
                'falta': falta,
                # Produto sem controle de lote não recebe lote nenhum, e a
                # separação do ERP o pula. Dizer isso é melhor do que uma
                # linha vazia, que se lê como "acabou o estoque".
                'sem_lote': not item.produto.controla_lote,
            })
        return linhas

    @staticmethod
    def _sugestao(item, filial_id) -> tuple[list[dict], Decimal]:
        """
        Os lotes que a FEFO indicaria, e quanto falta para fechar o item.

        `permitir_parcial` aqui é só para MOSTRAR: a tela precisa dizer
        "faltam 120 kg" em vez de recusar a página. Separar de verdade
        continua passando pela via estrita do `VendaService`.
        """
        if not item.produto.controla_lote:
            return [], ZERO

        pedido_qtd = (item.quantidade or ZERO) - (item.quantidade_atendida or ZERO)
        if pedido_qtd <= ZERO:
            return [], ZERO

        consumos = MovimentacaoService.selecionar_lotes_fifo(
            produto_id=item.produto_id,
            filial_id=filial_id,
            quantidade=pedido_qtd,
            permitir_parcial=True,
        )
        lotes = {
            l.pk: l for l in LoteProduto.objects.filter(
                pk__in=[c.lote_id for c in consumos],
            )
        }
        escolhidos = [
            {'lote': lotes[c.lote_id], 'quantidade': c.quantidade}
            for c in consumos if c.lote_id in lotes
        ]
        coberto = sum((e['quantidade'] for e in escolhidos), ZERO)
        return escolhidos, max(pedido_qtd - coberto, ZERO)

    # ── Ação ─────────────────────────────────────────────────────────────

    @staticmethod
    def separar(pedido, usuario):
        """
        Fecha a separação do pedido — delegando ao serviço de vendas.

        A REGRA DE SEPARAÇÃO NÃO É DO VERTICAL. Quem decide se um pedido
        pode ser separado, qual lote sai e como o documento nasce é o
        `VendaService`; esta tela é o posto de trabalho de quem faz. Uma
        cópia aqui daria dois caminhos para o mesmo ato, e um deles ficaria
        sem as regras que o outro ganhasse depois.
        """
        from apps.vendas.services.venda_service import VendaService

        try:
            return VendaService.separar_pedido(pedido, usuario)
        except DomainError:
            raise
        except Exception as erro:  # noqa: BLE001 — vira mensagem de tela
            raise DomainError(str(erro)) from erro
