"""
As vendas que podem entrar numa carga, e como elas viram itens de carga.

ESCOLHE-SE A VENDA, NÃO O PRODUTO
=================================

Digitar produto e quantidade para mercadoria que já foi vendida é redigitar o
que o pedido já diz — e todo redigitar é uma chance de a carga sair diferente
do que o cliente comprou. Aqui a pessoa marca a venda, e os itens dela entram
como estão.

PESO E VOLUME VÊM DO PRODUTO
============================

O item do pedido guarda quantidade e valor, não peso nem cubagem. Quem sabe
disso é o cadastro do produto. Produto sem peso cadastrado aparece com zero e
não trava a carga: o peso serve para conferência e para o MDF-e, e barrar o
carregamento por um cadastro incompleto pararia o caminhão por um problema de
retaguarda.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from apps.core.services.exceptions import DadosInvalidosError
from apps.fiscal.models import NaturezaOperacao
from apps.logistica.models import ItemCarga, Viagem
from apps.vendas.models.pedido import PedidoVenda

ZERO = Decimal('0')
# A PRECISAO DOS CAMPOS DE DESTINO. Peso e quantidade tem tres casas cada, e o
# produto delas tem seis -- que o campo recusa. Sem arredondar aqui, qualquer
# produto com peso fracionado travaria o carregamento por erro de validacao,
# num ponto em que ninguem entende o que aconteceu.
TRES_CASAS = Decimal('0.001')
QUATRO_CASAS = Decimal('0.0001')

# A venda entra na carga quando já é compromisso com o cliente. Rascunho e
# aguardando aprovação ainda podem mudar, e carregar o que pode mudar é
# prometer o que talvez não se cumpra.
CARREGAVEIS = (
    PedidoVenda.Status.CONFIRMADO,
    PedidoVenda.Status.EM_SEPARACAO,
    # FATURADO TAMBEM CARREGA: nada neste sistema marca um pedido como
    # ENTREGUE, entao faturar e' o fim da linha do lado comercial e o pedido
    # fica parado esperando quem o leve.
    PedidoVenda.Status.FATURADO,
    PedidoVenda.Status.PARCIALMENTE_FATURADO,
)


class VendasParaCargaService:

    # ── Listar ───────────────────────────────────────────────────────────

    @classmethod
    def disponiveis(cls, filial, busca: str = '', viagem=None) -> list[dict]:
        """
        As vendas que ainda não estão em nenhuma viagem.

        VENDA JÁ CARREGADA NÃO APARECE. É a regra que impede a mesma mercadoria
        de subir em dois caminhões — e o motivo de a lista consultar as cargas
        antes de mostrar qualquer coisa.
        """
        ja_em_carga = set(
            ItemCarga.objects
            .filter(pedido_venda__isnull=False)
            .exclude(viagem__status=Viagem.Status.CANCELADA)
            .values_list('pedido_venda_id', flat=True)
        )
        pedidos = (
            PedidoVenda.objects.filter(filial=filial, status__in=CARREGAVEIS)
            .exclude(pk__in=ja_em_carga)
            .select_related('cliente')
            .prefetch_related('itens__produto')
            .order_by('data_entrega_prevista', 'numero_pedido')
        )
        if busca:
            pedidos = pedidos.filter(
                Q(numero_pedido__icontains=busca)
                | Q(cliente__razao_social__icontains=busca)
                | Q(cliente__nome_fantasia__icontains=busca)
            )
        return [cls._linha(pedido) for pedido in pedidos]

    @classmethod
    def _linha(cls, pedido) -> dict:
        itens = [cls._item(item) for item in pedido.itens.all()]
        return {
            'pedido': pedido,
            'cliente': pedido.cliente,
            'nota': cls.nota_do_pedido(pedido),
            'itens': itens,
            'quantidade': sum((i['quantidade'] for i in itens), ZERO),
            'peso': sum((i['peso'] for i in itens), ZERO),
            'volume': sum((i['volume'] for i in itens), ZERO),
            'valor': sum((i['valor'] for i in itens), ZERO),
            'entrega': pedido.data_entrega_prevista,
        }

    @staticmethod
    def _item(item) -> dict:
        produto = item.produto
        quantidade = item.quantidade or ZERO
        peso_unitario = getattr(produto, 'peso_bruto', None) or getattr(
            produto, 'peso_liquido', None,
        ) or ZERO
        volume_unitario = Decimal(str(getattr(produto, 'volume_cubagem', 0) or 0))
        return {
            'item': item,
            'produto': produto,
            'quantidade': quantidade,
            'valor_unitario': item.valor_unitario or ZERO,
            'valor': item.valor_total or ZERO,
            'peso': (peso_unitario * quantidade).quantize(TRES_CASAS),
            'volume': (volume_unitario * quantidade).quantize(QUATRO_CASAS),
        }

    @staticmethod
    def nota_do_pedido(pedido):
        """
        A NF-e que ampara este pedido, se já houver.

        HOJE NORMALMENTE NÃO HÁ: nada neste sistema emite NF-e a partir de
        pedido de venda ainda — `origem_tipo` só conhece PDV, transferência,
        CT-e e MDF-e. A coluna existe e fica vazia até a emissão ser
        construída; mostrar em branco é honesto, inventar um número não.
        """
        from apps.financeiro.constants.enums import StatusDocumentoFiscal
        from apps.financeiro.models.fiscal import DocumentoFiscal

        return (
            DocumentoFiscal.objects
            .filter(origem_tipo='pedido_venda', origem_id=pedido.pk)
            # Nota cancelada, rejeitada ou denegada nao ampara carga nenhuma;
            # mostra-la faria a carga parecer documentada quando nao esta'.
            .exclude(status__in=(
                StatusDocumentoFiscal.CANCELADA,
                StatusDocumentoFiscal.REJEITADA,
                StatusDocumentoFiscal.DENEGADA,
                StatusDocumentoFiscal.INUTILIZADA,
            ))
            .order_by('-id')
            .first()
        )

    # ── Carregar ─────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def adicionar_vendas(cls, viagem: Viagem, pedidos, natureza=None) -> int:
        """
        Põe os itens das vendas escolhidas na carga. Devolve quantos entraram.

        VÁRIAS DE UMA VEZ, porque é assim que a doca trabalha: escolhe-se tudo
        que vai naquele caminhão e confere-se junto, não uma venda por vez.
        """
        from apps.logistica.services.viagem import ViagemService

        pedidos = list(pedidos)
        if not pedidos:
            raise DadosInvalidosError('Escolha ao menos uma venda para carregar.')

        natureza = natureza or cls._natureza_de_venda(viagem.filial)
        criados = 0
        for pedido in pedidos:
            nota = cls.nota_do_pedido(pedido)
            for dados in cls._linha(pedido)['itens']:
                if dados['quantidade'] <= ZERO:
                    continue
                item = ViagemService.adicionar_item(viagem, {
                    'natureza': natureza,
                    'produto': dados['produto'],
                    'cliente': pedido.cliente,
                    'pedido_venda': pedido,
                    'quantidade': dados['quantidade'],
                    'valor_unitario': dados['valor_unitario'],
                    'peso_kg': dados['peso'],
                })
                # O ELO ATE' A NOTA fica gravado na linha: viagem → carga →
                # documento fiscal → cliente.
                if nota is not None:
                    item.documento_fiscal = nota
                    item.save(update_fields=['documento_fiscal', 'updated_at'])
                criados += 1
        if not criados:
            raise DadosInvalidosError(
                'As vendas escolhidas não têm itens com quantidade para carregar.'
            )
        return criados

    @staticmethod
    def _natureza_de_venda(filial):
        naturezas = list(
            NaturezaOperacao.objects.for_filial(filial).filter(
                especie=NaturezaOperacao.Especie.VENDA, ativo=True,
            )
        )
        if not naturezas:
            raise DadosInvalidosError(
                'Nenhuma natureza de operação cadastrada para venda. '
                'Cadastre em Fiscal › Naturezas de operação.'
            )
        if len(naturezas) > 1:
            raise DadosInvalidosError(
                f'Há {len(naturezas)} naturezas de venda — escolha qual usar '
                'ao carregar.'
            )
        return naturezas[0]
