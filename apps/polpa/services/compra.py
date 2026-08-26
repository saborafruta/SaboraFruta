"""
Da produção para compras: o que falta de insumo, somado e pronto para comprar.

A CONTA, escrita uma vez:

    necessário = Σ (consumo da receita × tamanho) das ordens em aberto
    livre      = estoque físico − reservas de OUTRAS ordens
    déficit    = necessário − livre   (nunca negativo)

SOMAR ANTES DE COMPARAR é o que este módulo faz e a necessidade por ordem não
faz. `OrdemPolpaService.necessidade` olha uma batida por vez, e cada uma vê o
estoque INTEIRO como se fosse só dela: três batidas de acerola que precisam de
200 kg cada, com 500 kg no galpão, aparecem as três cobertas. Somadas, faltam
100. Comprar por ordem também compraria morango três vezes, com três fretes e
sem escala de negociação.

"RESERVAS DE OUTRAS" É O DETALHE QUE INVERTE O SINAL. `quantidade_disponivel`
já desconta TODAS as reservas, inclusive as que estas mesmas ordens fizeram
quando entraram em produção. Usá-lo aqui faria reservar material AUMENTAR o
déficit — a ação que resolve o problema pioraria o indicador, e o comprador
compraria de novo o que já está separado no galpão. Aqui a reserva feita PARA
estas ordens volta para o livre.

NÃO CRIA PEDIDO DE COMPRA SOZINHO. A requisição diz o que falta; fornecedor,
preço e condição são decisão de compras. Um pedido gerado com fornecedor
adivinhado é um documento que o comprador refaz inteiro — e enquanto isso ele
circula parecendo oficial.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.estoque.models import Estoque
from apps.polpa.models import (
    ItemRequisicaoInsumo, OrdemPolpa, RequisicaoInsumo, ReservaInsumo,
)

ZERO = Decimal('0')

# Ordens que ainda vão consumir. Produzida e cancelada não consomem mais nada;
# contá-las mandaria comprar para batida que já aconteceu.
ORDENS_EM_ABERTO = (
    OrdemPolpa.Situacao.PLANEJADA,
    OrdemPolpa.Situacao.LIBERADA,
    OrdemPolpa.Situacao.EM_PRODUCAO,
    OrdemPolpa.Situacao.PAUSADA,
)


@dataclass
class Necessidade:
    """Uma linha: um insumo e o que falta dele."""
    produto: object
    necessario: Decimal = ZERO
    estoque_fisico: Decimal = ZERO
    reservado_nosso: Decimal = ZERO
    reservado_total: Decimal = ZERO
    ordens: list = field(default_factory=list)

    @property
    def unidade(self) -> str:
        return getattr(self.produto.unidade_medida, 'sigla', '')

    @property
    def livre(self) -> Decimal:
        """
        Estoque que ESTAS ordens podem contar.

        Soma de volta o que elas mesmas reservaram: aquele material está
        fisicamente separado para este trabalho, não é falta.
        """
        outros = self.reservado_total - self.reservado_nosso
        livre = self.estoque_fisico - max(outros, ZERO)
        return livre if livre > ZERO else ZERO

    @property
    def deficit(self) -> Decimal:
        falta = self.necessario - self.livre
        return falta.quantize(Decimal('0.0001')) if falta > ZERO else ZERO

    @property
    def falta(self) -> bool:
        return self.deficit > ZERO


class CompraService:

    # ── Necessidade ──────────────────────────────────────────────────────

    @classmethod
    def necessidade(cls, filial) -> list[Necessidade]:
        """
        O que as ordens em aberto vão consumir, contra o que existe.

        Devolve TODOS os insumos das ordens abertas, e não só os que faltam:
        ver que um insumo está coberto é informação — some da lista e alguém
        pergunta se ele foi esquecido.
        """
        ordens = list(
            OrdemPolpa.objects.for_filial(filial)
            .filter(situacao__in=ORDENS_EM_ABERTO)
            .select_related('ordem', 'ordem__ficha_tecnica', 'receita')
        )
        if not ordens:
            return []

        linhas: dict[int, Necessidade] = {}
        for op in ordens:
            ficha = op.ordem.ficha_tecnica
            base = ficha.quantidade_produzida or ZERO
            if base <= ZERO:
                continue
            fator = op.quantidade_planejada / base

            for item in ficha.itens.select_related(
                'materia_prima', 'materia_prima__unidade_medida',
            ):
                produto = item.materia_prima
                linha = linhas.setdefault(
                    produto.pk, Necessidade(produto=produto),
                )
                linha.necessario += item.quantidade_com_perda() * fator
                if op not in linha.ordens:
                    linha.ordens.append(op)

        cls._preencher_estoque(filial, linhas)
        cls._preencher_reservas(ordens, linhas)

        resultado = list(linhas.values())
        # O que falta primeiro; empate pelo nome, para a lista não dançar a
        # cada abertura.
        resultado.sort(key=lambda l: (-l.deficit, l.produto.descricao))
        return resultado

    @staticmethod
    def _preencher_estoque(filial, linhas: dict) -> None:
        saldos = Estoque.objects.filter(
            filial=filial, produto_id__in=linhas.keys(),
        ).values('produto_id', 'quantidade_atual', 'quantidade_reservada')
        for saldo in saldos:
            linha = linhas[saldo['produto_id']]
            linha.estoque_fisico = saldo['quantidade_atual'] or ZERO
            linha.reservado_total = saldo['quantidade_reservada'] or ZERO

    @staticmethod
    def _preencher_reservas(ordens, linhas: dict) -> None:
        """Quanto destas ordens já está separado, por insumo."""
        nossas = defaultdict(lambda: ZERO)
        reservas = ReservaInsumo.all_objects.filter(
            ordem__in=ordens, status=ReservaInsumo.Status.ATIVA,
        ).values('produto_id', 'quantidade')
        for reserva in reservas:
            nossas[reserva['produto_id']] += reserva['quantidade'] or ZERO
        for produto_id, quantidade in nossas.items():
            if produto_id in linhas:
                linhas[produto_id].reservado_nosso = quantidade

    # ── Requisição ───────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def gerar_requisicao(cls, filial, linhas: list[Necessidade],
                         usuario=None, observacao: str = '') -> RequisicaoInsumo:
        """
        Cria a requisição com tudo que está em déficit.

        SÓ O QUE FALTA entra. Requisitar o que já existe faria compras negociar
        material que está no galpão, e a primeira vez que isso acontece o
        comprador para de confiar na lista inteira.
        """
        faltando = [l for l in linhas if l.falta]
        if not faltando:
            raise DomainError(
                'Nenhum insumo em déficit — não há o que requisitar.'
            )

        requisicao = RequisicaoInsumo.objects.create(
            filial=filial, criado_por=usuario,
            observacao=observacao or (
                'Gerada a partir da necessidade das ordens em aberto.'
            ),
        )
        ItemRequisicaoInsumo.objects.bulk_create([
            ItemRequisicaoInsumo(
                requisicao=requisicao,
                produto=l.produto,
                descricao=l.produto.descricao,
                codigo=l.produto.codigo or '',
                unidade=l.unidade,
                quantidade=l.deficit,
                necessario=l.necessario.quantize(Decimal('0.0001')),
                disponivel=l.livre.quantize(Decimal('0.0001')),
                observacao=f'{len(l.ordens)} ordem(ns) em aberto',
            )
            for l in faltando
        ])
        return requisicao

    # ── Pedido de compra ─────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def gerar_pedido_compra(cls, requisicao: RequisicaoInsumo,
                            fornecedor, usuario=None):
        """
        Transforma a requisição num pedido de compra de verdade.

        O PREÇO VEM DO CUSTO e é PONTO DE PARTIDA — quem negocia é compras, e é
        lá que o valor final se acerta. Deixar zero seria pior: um pedido com
        valor zerado passa despercebido na aprovação.
        """
        from apps.compras.models import ItemPedidoCompra, PedidoCompra

        if requisicao.pedido_compra_id:
            raise DomainError(
                f'Esta requisição já virou o pedido de compra '
                f'{requisicao.pedido_compra}.'
            )
        if requisicao.status == RequisicaoInsumo.Status.CANCELADA:
            raise DomainError('Requisição cancelada não gera pedido de compra.')

        itens = list(requisicao.itens.select_related('produto'))
        if not itens:
            raise DomainError('Esta requisição não tem itens.')

        pedido = PedidoCompra.objects.create(
            filial=requisicao.filial,
            fornecedor=fornecedor,
            usuario=usuario,
            numero_pedido=cls._proximo_numero(requisicao.filial),
            data_emissao=timezone.now(),
            status=PedidoCompra.Status.RASCUNHO,
            observacao=(
                f'Gerado da requisição de insumo #{requisicao.numero:04d} '
                f'(produção de polpa).'
            ),
        )

        linhas = []
        for indice, item in enumerate(itens, start=1):
            unitario = (
                item.produto.preco_custo_medio
                or item.produto.preco_custo
                or ZERO
            )
            linha = ItemPedidoCompra(
                pedido=pedido,
                produto=item.produto,
                numero_item=indice,
                quantidade=item.quantidade,
                valor_unitario=unitario,
                valor_bruto=ZERO,
                valor_total=ZERO,
                observacao=item.observacao[:255],
            )
            linha.calcular_totais()
            linhas.append(linha)
        ItemPedidoCompra.objects.bulk_create(linhas)

        pedido.valor_produtos = sum((l.valor_total for l in linhas), ZERO)
        pedido.valor_total = pedido.valor_produtos
        pedido.save(update_fields=['valor_produtos', 'valor_total'])

        requisicao.pedido_compra = pedido
        # A REQUISIÇÃO NÃO VIRA "ATENDIDA" AQUI. Atendida é quando o material
        # CHEGA; marcar na emissão faria o PCP achar que tem morango no galpão
        # enquanto ele ainda está na roça.
        requisicao.save(update_fields=['pedido_compra'])
        return pedido

    @staticmethod
    def _proximo_numero(filial) -> str:
        from apps.compras.models import PedidoCompra

        # `objects`, e nao `all_objects`: `PedidoCompra` herda o manager
        # padrao de `FilialScopedModel` e nao declara o irrestrito.
        ultimo = (
            PedidoCompra.objects.filter(filial=filial)
            .order_by('-pk').values_list('numero_pedido', flat=True).first()
        )
        try:
            seguinte = int(str(ultimo).split('-')[-1]) + 1
        except (TypeError, ValueError):
            seguinte = 1
        return f'PC-{seguinte:06d}'

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def resumo(linhas: list[Necessidade]) -> dict:
        faltando = [l for l in linhas if l.falta]
        return {
            'insumos': len(linhas),
            'faltando': len(faltando),
            'cobertos': len(linhas) - len(faltando),
            'ordens': len({
                op.pk for l in linhas for op in l.ordens
            }),
        }
