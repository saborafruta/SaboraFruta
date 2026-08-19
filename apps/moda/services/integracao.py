"""
As duas pontes que faltavam entre o vertical e o resto do ERP.

O PRINCÍPIO É "DIGITAR UMA VEZ", e ele já valia da engenharia ao financeiro:
a ficha alimenta o custo, a OP lê o pedido, o WIP lê o fluxo, o dashboard lê
tudo. Faltavam dois pedaços onde a informação MORRIA e alguém precisava
redigitar do outro lado:

  1. COMPRAS — a requisição de material dizia o que falta e parava ali. O
     comprador abria o pedido de compra e redigitava linha por linha, com o
     risco de trocar quantidade ou produto no caminho.

  2. ESTOQUE — o corte gravava o consumo real do tecido e o saldo do estoque
     continuava o mesmo. Alguém tinha que dar baixa à mão, e até dar, o
     sistema achava que havia 200 m de um rolo que já virou peça.

O SEGUNDO É PIOR QUE REDIGITAÇÃO: é número errado. O painel de necessidade
lê esse saldo, e um saldo alto demais esconde a falta de material até o
corte parar.

POR QUE A BAIXA É UM BOTÃO, e não automática ao marcar o corte como cortado:
mexer no estoque de outro módulo é efeito colateral grande para acontecer
sem alguém mandar. O consumo continua sendo digitado UMA vez, no corte; o
botão só propaga. E é reversível — cancelar o corte estorna.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError

ZERO = Decimal('0')


class IntegracaoService:

    # ── Requisição → Pedido de compra ────────────────────────────────────

    @classmethod
    @transaction.atomic
    def gerar_pedido_compra(cls, requisicao, fornecedor, usuario):
        """
        Transforma a requisição num pedido de compra de verdade.

        Só entram linhas LIGADAS a um produto de estoque: comprar exige um
        produto cadastrado, e material da ficha ainda sem vínculo não tem o
        que virar item. As linhas soltas ficam na requisição, visíveis, em
        vez de sumirem num pedido incompleto.

        O preço vem do custo do produto e é um PONTO DE PARTIDA — quem
        negocia é compras, e é lá que o valor final é acertado. Deixar zero
        seria pior: um pedido com valor zerado passa despercebido na
        aprovação.
        """
        from apps.compras.models import ItemPedidoCompra, PedidoCompra

        if requisicao.pedido_compra_id:
            raise DomainError(
                f'Esta requisição já virou o pedido de compra '
                f'{requisicao.pedido_compra.numero_pedido}.'
            )
        if requisicao.status == requisicao.Status.CANCELADA:
            raise DomainError('Requisição cancelada não gera pedido de compra.')

        linhas = [i for i in requisicao.itens.all() if i.produto_id]
        if not linhas:
            raise DomainError(
                'Nenhum item desta requisição está ligado a um produto de '
                'estoque. Ligue o material à ficha técnica antes de comprar.'
            )

        pedido = PedidoCompra.objects.create(
            filial=requisicao.filial,
            fornecedor=fornecedor,
            usuario=usuario,
            numero_pedido=cls._proximo_numero_compra(requisicao.filial),
            data_emissao=timezone.now(),
            status=PedidoCompra.Status.RASCUNHO,
            observacoes=(
                f'Gerado da requisição de material #{requisicao.numero:04d} '
                f'do vertical Moda.'
            ),
        )

        itens = []
        for indice, linha in enumerate(linhas, start=1):
            unitario = (
                linha.produto.preco_custo_medio
                or linha.produto.preco_custo
                or ZERO
            )
            item = ItemPedidoCompra(
                pedido=pedido,
                produto=linha.produto,
                numero_item=indice,
                quantidade=linha.quantidade,
                valor_unitario=unitario,
                valor_bruto=ZERO,
                valor_total=ZERO,
                observacao=linha.observacao[:255],
            )
            item.calcular_totais()
            itens.append(item)

        ItemPedidoCompra.objects.bulk_create(itens)

        pedido.valor_produtos = sum((i.valor_total for i in itens), ZERO)
        pedido.valor_total = pedido.valor_produtos
        pedido.save(update_fields=['valor_produtos', 'valor_total'])

        requisicao.pedido_compra = pedido
        # A requisição NÃO vira "atendida" aqui.
        # Atendida é quando o material CHEGA: marcar na emissão faria o PCP
        # achar que o tecido está na prateleira porque alguém mandou comprar.
        requisicao.save(update_fields=['pedido_compra'])

        return pedido, len(linhas), requisicao.itens.count() - len(linhas)

    @staticmethod
    def _proximo_numero_compra(filial) -> str:
        from apps.compras.models import PedidoCompra

        ano = timezone.localdate().year
        prefixo = f'MOD{ano}'
        ultimo = (
            PedidoCompra.all_objects
            .filter(filial=filial, numero_pedido__startswith=prefixo)
            .order_by('-numero_pedido')
            .values_list('numero_pedido', flat=True)
            .first()
        )
        sequencial = 1
        if ultimo:
            try:
                sequencial = int(ultimo[len(prefixo):]) + 1
            except ValueError:
                sequencial = 1
        return f'{prefixo}{sequencial:05d}'

    # ── Corte → baixa de estoque ─────────────────────────────────────────

    @classmethod
    def material_do_corte(cls, corte):
        """
        Qual produto de estoque este corte consome, e quanto.

        O tecido sai da FICHA TÉCNICA do produto, e não de um campo do
        corte: é o mesmo material que a necessidade calculou e que a
        requisição comprou. Ler de outro lugar aqui abriria a porta para o
        corte baixar um produto e o PCP ter reservado outro.
        """
        from apps.moda.models import MaterialFicha

        ficha = corte.ordem.ficha
        if ficha is None:
            return None, ZERO, 'Produto sem ficha técnica cadastrada.'

        material = (
            ficha.materiais
            .filter(tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL)
            .exclude(produto_estoque__isnull=True)
            .first()
        )
        if material is None:
            return None, ZERO, (
                'O tecido principal da ficha não está ligado a um produto de '
                'estoque.'
            )

        quantidade = corte.consumo_real or ZERO
        if quantidade <= 0:
            return material.produto_estoque, ZERO, (
                'O consumo real do corte ainda não foi informado.'
            )
        return material.produto_estoque, quantidade, ''

    @classmethod
    @transaction.atomic
    def baixar_estoque_do_corte(cls, corte, usuario):
        """
        Dá baixa no tecido que o corte consumiu.

        Idempotente pelo carimbo: dois cliques no botão não tiram o tecido
        duas vezes. É a proteção que importa aqui — estoque baixado a mais
        não aparece como erro, aparece como "sumiu material".
        """
        from apps.estoque.models.estoque import MovimentacaoEstoque
        from apps.estoque.services.movimentacao_service import MovimentacaoService

        if corte.estoque_baixado_em:
            raise DomainError('O estoque deste corte já foi baixado.')
        if corte.status != corte.Status.CORTADO:
            raise DomainError(
                'Só o corte concluído dá baixa: marcar como cortado é o que '
                'diz que o tecido saiu do rolo.'
            )

        produto, quantidade, problema = cls.material_do_corte(corte)
        if problema:
            raise DomainError(problema)

        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=corte.filial_id,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.PRODUCAO_SAIDA,
            quantidade=quantidade,
            usuario_id=usuario.pk,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.ORDEM_PRODUCAO,
            documento_id=corte.ordem_id,
            documento_numero=corte.ordem.numero,
            observacao=(
                f'Corte #{corte.numero:04d} — consumo real de tecido'
            ),
            permitir_sem_lote=True,
        )

        corte.estoque_baixado_em = timezone.now()
        corte.save(update_fields=['estoque_baixado_em'])
        return produto, quantidade

    @classmethod
    @transaction.atomic
    def estornar_estoque_do_corte(cls, corte, usuario):
        """
        Devolve ao estoque o que a baixa tirou.

        Existe porque corte é cancelado de verdade: enfesto errado, tecido
        trocado. Sem estorno, a única saída seria um ajuste manual — que é
        exatamente a redigitação que este trabalho veio eliminar.
        """
        from apps.estoque.models.estoque import MovimentacaoEstoque
        from apps.estoque.services.movimentacao_service import MovimentacaoService

        if not corte.estoque_baixado_em:
            raise DomainError('Este corte não tem baixa de estoque para estornar.')

        produto, quantidade, problema = cls.material_do_corte(corte)
        if problema:
            raise DomainError(problema)

        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=corte.filial_id,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.PRODUCAO_ENTRADA,
            quantidade=quantidade,
            usuario_id=usuario.pk,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.ORDEM_PRODUCAO,
            documento_id=corte.ordem_id,
            documento_numero=corte.ordem.numero,
            observacao=f'Estorno da baixa do corte #{corte.numero:04d}',
            permitir_sem_lote=True,
        )

        corte.estoque_baixado_em = None
        corte.save(update_fields=['estoque_baixado_em'])
        return produto, quantidade
