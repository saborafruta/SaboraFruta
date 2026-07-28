"""
VendaService — máquina de estados do pedido de venda + FEFO automático + reserva de estoque.

Regras:
1. Aprovação opcional se exceder limite de crédito ou grupo de desconto.
2. Confirmar pedido RESERVA estoque (incrementa quantidade_reservada).
3. Separar consome lotes via FEFO (cria ItemSeparacao por lote).
4. Faturar dá BAIXA real no estoque e libera a reserva.
5. Cancelar em qualquer status antes de faturar libera a reserva.
6. Devolver retorna itens ao estoque (se aplicável).
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.core.services.exceptions import (
    DadosInvalidosError, EstoqueInsuficienteError, PermissaoNegadaError,
)
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.pdv.services.produto_vendavel_service import ProdutoVendavelService
from apps.produtos.services.preco_service import PrecoService
from apps.vendas.models import (
    DevolucaoVenda, ItemDevolucao, ItemPedidoVenda, ItemSeparacao,
    PedidoVenda, SeparacaoPedido,
)

logger = logging.getLogger(__name__)


class VendaService:
    """Serviço único de gestão de pedidos de venda."""

    # ----------------------------------------------------------------------
    # Criação e manutenção do pedido
    # ----------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def criar_pedido(
        cls, filial, usuario, cliente, tipo=PedidoVenda.Tipo.PEDIDO,
        representante=None, tabela_preco=None, observacao='',
    ) -> PedidoVenda:
        """Cria pedido em rascunho."""
        if cliente.bloqueado:
            raise DadosInvalidosError(f'Cliente bloqueado: {cliente.motivo_bloqueio}')

        tabela_preco = PrecoService.tabela_cliente_vigente(
            cliente=cliente,
            filial=filial,
            tabela=cliente.tabela_preco or tabela_preco,
        )
        numero = cls._gerar_numero(filial)
        pedido = PedidoVenda.objects.create(
            filial=filial,
            numero_pedido=numero,
            cliente=cliente,
            representante=representante,
            tabela_preco=tabela_preco,
            usuario=usuario,
            tipo=tipo,
            status=PedidoVenda.Status.RASCUNHO,
            data_emissao=timezone.now(),
            observacao=observacao,
        )
        return pedido

    @staticmethod
    def _gerar_numero(filial) -> str:
        ultimo = PedidoVenda.objects.filter(filial=filial).order_by('-pk').first()
        if not ultimo or not ultimo.numero_pedido.startswith('PV-'):
            return 'PV-0000001'
        try:
            num = int(ultimo.numero_pedido.split('-')[1]) + 1
        except (ValueError, IndexError):
            num = 1
        return f'PV-{num:07d}'

    @classmethod
    @transaction.atomic
    def adicionar_item(
        cls, pedido: PedidoVenda, produto, quantidade: Decimal,
        valor_unitario: Decimal | None = None, percentual_desconto: Decimal = Decimal('0'),
    ) -> ItemPedidoVenda:
        """Adiciona ou atualiza item no pedido (rascunho)."""
        if pedido.status != PedidoVenda.Status.RASCUNHO:
            raise DadosInvalidosError('Só é possível adicionar itens em pedidos em rascunho.')
        if quantidade <= 0:
            raise DadosInvalidosError('Quantidade deve ser positiva.')

        # Preço: tabela de preço > preço de venda do produto
        contrato = ProdutoVendavelService.validar_venda(
            produto=produto,
            filial=pedido.filial,
            quantidade=quantidade,
            cliente=pedido.cliente,
            tabela_preco=pedido.tabela_preco,
        )

        if valor_unitario is None:
            valor_unitario = contrato['preco_aplicado']

        numero_item = pedido.itens.count() + 1
        item = ItemPedidoVenda(
            pedido=pedido,
            produto=produto,
            numero_item=numero_item,
            quantidade=quantidade,
            valor_unitario=valor_unitario,
            percentual_desconto=percentual_desconto,
            custo_unitario_snapshot=contrato['custo_atual'],
        )
        item.calcular_totais()
        item.save()

        pedido.recalcular_totais()
        pedido.save()
        return item

    @classmethod
    @transaction.atomic
    def remover_item(cls, pedido: PedidoVenda, item_id: int):
        if pedido.status != PedidoVenda.Status.RASCUNHO:
            raise DadosInvalidosError('Só é possível remover itens em pedidos em rascunho.')
        ItemPedidoVenda.objects.filter(pedido=pedido, pk=item_id).delete()
        pedido.recalcular_totais()
        pedido.save()

    # ----------------------------------------------------------------------
    # Transições de estado
    # ----------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def confirmar_pedido(cls, pedido: PedidoVenda, usuario) -> PedidoVenda:
        """
        Confirma pedido:
        1. Valida disponibilidade de estoque em todos os itens
        2. Valida limite de crédito do cliente
        3. Reserva estoque (quantidade_reservada += qtd do item)
        4. Muda status para CONFIRMADO
        """
        if not pedido.pode_confirmar:
            raise DadosInvalidosError(
                f'Pedido não pode ser confirmado no status "{pedido.get_status_display()}".'
            )
        if not pedido.itens.exists():
            raise DadosInvalidosError('Pedido sem itens não pode ser confirmado.')

        # Validar crédito
        cls._validar_credito(pedido)

        # Validar + reservar estoque
        for item in pedido.itens.select_related('produto'):
            ProdutoVendavelService.validar_venda(
                produto=item.produto,
                filial=pedido.filial,
                quantidade=item.quantidade,
            )
            try:
                MovimentacaoService.reservar_estoque(
                    produto_id=item.produto_id,
                    filial_id=pedido.filial_id,
                    quantidade=item.quantidade,
                    permitir_sem_estoque=item.produto.permite_venda_sem_estoque,
                )
                item.quantidade_reservada = item.quantidade
                item.save(update_fields=['quantidade_reservada', 'updated_at'])
            except EstoqueInsuficienteError as exc:
                raise EstoqueInsuficienteError(f'Produto "{item.produto}": {exc}')

        pedido.status = PedidoVenda.Status.CONFIRMADO
        pedido.save(update_fields=['status', 'updated_at'])
        return pedido

    @staticmethod
    def _validar_credito(pedido: PedidoVenda):
        cliente = pedido.cliente
        if cliente.limite_credito > 0:
            saldo_apos = cliente.saldo_devedor + pedido.valor_total
            if saldo_apos > cliente.limite_credito:
                raise DadosInvalidosError(
                    f'Limite de crédito excedido. '
                    f'Limite: R$ {cliente.limite_credito} | '
                    f'Saldo devedor após: R$ {saldo_apos:.2f}.'
                )

    @classmethod
    @transaction.atomic
    def separar_pedido(cls, pedido: PedidoVenda, usuario) -> SeparacaoPedido:
        """
        Cria separação consumindo lotes via FEFO.
        Não dá baixa ainda — só identifica os lotes.
        """
        if not pedido.pode_separar:
            raise DadosInvalidosError(
                f'Pedido não pode ser separado no status "{pedido.get_status_display()}".'
            )

        separacao = SeparacaoPedido.objects.create(
            filial=pedido.filial,
            pedido=pedido,
            numero=cls._gerar_numero_separacao(pedido.filial),
            status=SeparacaoPedido.Status.EM_ANDAMENTO,
            usuario_separador=usuario,
        )

        for item in pedido.itens.select_related('produto'):
            if not item.produto.controla_lote:
                # Produto sem controle de lote — separação simples
                continue
            consumos = MovimentacaoService.selecionar_lotes_fifo(
                produto_id=item.produto_id,
                filial_id=pedido.filial_id,
                quantidade=item.quantidade,
            )
            for c in consumos:
                ItemSeparacao.objects.create(
                    separacao=separacao,
                    item_pedido=item,
                    lote_id=c.lote_id,
                    quantidade_separada=c.quantidade,
                )

        separacao.status = SeparacaoPedido.Status.CONCLUIDA
        separacao.data_fim = timezone.now()
        separacao.save(update_fields=['status', 'data_fim'])

        pedido.status = PedidoVenda.Status.EM_SEPARACAO
        pedido.save(update_fields=['status', 'updated_at'])
        return separacao

    @staticmethod
    def _gerar_numero_separacao(filial) -> str:
        ultima = SeparacaoPedido.objects.filter(filial=filial).order_by('-pk').first()
        if not ultima or not ultima.numero.startswith('SEP-'):
            return 'SEP-0000001'
        try:
            num = int(ultima.numero.split('-')[1]) + 1
        except (ValueError, IndexError):
            num = 1
        return f'SEP-{num:07d}'

    @classmethod
    @transaction.atomic
    def faturar_pedido(cls, pedido: PedidoVenda, usuario) -> PedidoVenda:
        """
        Dá baixa real no estoque, libera reserva e muda para FATURADO.
        Se houver separação, usa os lotes identificados; senão, usa FEFO direto.
        """
        if not pedido.pode_faturar:
            raise DadosInvalidosError(
                f'Pedido não pode ser faturado no status "{pedido.get_status_display()}".'
            )

        separacao = pedido.separacoes.filter(
            status=SeparacaoPedido.Status.CONCLUIDA,
        ).order_by('-data_inicio').first()

        for item in pedido.itens.select_related('produto'):
            # Liberar reserva primeiro
            MovimentacaoService.liberar_reserva(
                produto_id=item.produto_id,
                filial_id=pedido.filial_id,
                quantidade=item.quantidade,
            )

            # Executar saída real
            if separacao and item.produto.controla_lote:
                itens_sep = separacao.itens.filter(item_pedido=item)
                for sep in itens_sep:
                    MovimentacaoService.registrar_movimentacao(
                        produto_id=item.produto_id,
                        filial_id=pedido.filial_id,
                        tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
                        quantidade=sep.quantidade_separada,
                        usuario_id=usuario.pk,
                        lote_id=sep.lote_id,
                        valor_unitario=item.valor_unitario,
                        documento_tipo=MovimentacaoEstoque.DocumentoTipo.PEDIDO_VENDA,
                        documento_id=pedido.pk,
                        documento_numero=pedido.numero_pedido,
                    )
            else:
                # Produto sem controle de lote ou sem separação prévia → FEFO direto
                MovimentacaoService.registrar_saida_fefo(
                    produto_id=item.produto_id,
                    filial_id=pedido.filial_id,
                    quantidade=item.quantidade,
                    usuario_id=usuario.pk,
                    tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
                    documento_tipo=MovimentacaoEstoque.DocumentoTipo.PEDIDO_VENDA,
                    documento_id=pedido.pk,
                    documento_numero=pedido.numero_pedido,
                )

            item.quantidade_atendida = item.quantidade
            item.quantidade_reservada = Decimal('0')
            item.quantidade_faturada = item.quantidade
            item.save(update_fields=[
                'quantidade_atendida',
                'quantidade_reservada',
                'quantidade_faturada',
                'updated_at',
            ])

        # Atualizar saldo devedor do cliente
        pedido.cliente.saldo_devedor = F('saldo_devedor') + pedido.valor_total
        pedido.cliente.save(update_fields=['saldo_devedor', 'updated_at'])

        pedido.status = PedidoVenda.Status.FATURADO
        pedido.save(update_fields=['status', 'updated_at'])

        # Atualiza o padrão de recompra do cliente (CRM). Falha aqui nunca
        # pode derrubar o faturamento — o serviço já engole a exceção.
        from apps.crm.services import RecompraService
        RecompraService.recalcular_cliente_da_venda(pedido.filial, pedido.cliente_id)

        return pedido

    @classmethod
    @transaction.atomic
    def cancelar_pedido(cls, pedido: PedidoVenda, usuario, motivo: str) -> PedidoVenda:
        """Cancela pedido. Se estava reservado, libera a reserva."""
        if not pedido.pode_cancelar:
            raise DadosInvalidosError(
                f'Pedido não pode ser cancelado no status "{pedido.get_status_display()}".'
            )
        if not motivo.strip():
            raise DadosInvalidosError('Cancelamento exige motivo.')

        # Liberar reservas se o pedido estava reservado
        if pedido.status in (
            PedidoVenda.Status.CONFIRMADO, PedidoVenda.Status.EM_SEPARACAO,
        ):
            for item in pedido.itens.select_related('produto'):
                MovimentacaoService.liberar_reserva(
                    produto_id=item.produto_id,
                    filial_id=pedido.filial_id,
                    quantidade=item.quantidade,
                    tolerar_ausente=True,
                )
                item.quantidade_reservada = Decimal('0')
                item.save(update_fields=['quantidade_reservada', 'updated_at'])

        pedido.status = PedidoVenda.Status.CANCELADO
        pedido.motivo_cancelamento = motivo
        pedido.save(update_fields=['status', 'motivo_cancelamento', 'updated_at'])
        return pedido

    # ----------------------------------------------------------------------
    # Devolução
    # ----------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def criar_devolucao(
        cls, pedido: PedidoVenda, usuario, motivo: str, itens_devolvidos: list[dict],
        descricao: str = '',
    ) -> DevolucaoVenda:
        """
        Cria devolução retornando produtos ao estoque.
        itens_devolvidos = [{'item_pedido_id': int, 'quantidade': Decimal, 'retornar_ao_estoque': bool}, ...]
        """
        if pedido.status != PedidoVenda.Status.FATURADO:
            raise DadosInvalidosError(
                'Só é possível devolver pedidos faturados.'
            )
        if not itens_devolvidos:
            raise DadosInvalidosError('Informe ao menos um item.')

        devolucao = DevolucaoVenda.objects.create(
            filial=pedido.filial,
            pedido=pedido,
            numero=cls._gerar_numero_devolucao(pedido.filial),
            motivo=motivo,
            descricao=descricao,
            status=DevolucaoVenda.Status.APROVADA,
            data_devolucao=timezone.now().date(),
            usuario=usuario,
            aprovado_por=usuario,
        )

        valor_total = Decimal('0')
        for dev in itens_devolvidos:
            item = ItemPedidoVenda.objects.get(
                pk=dev['item_pedido_id'], pedido=pedido,
            )
            qtd = Decimal(str(dev['quantidade']))
            if qtd <= 0 or qtd > item.quantidade:
                raise DadosInvalidosError(
                    f'Quantidade inválida para item {item.numero_item}.'
                )

            retornar = dev.get('retornar_ao_estoque', True)
            ItemDevolucao.objects.create(
                devolucao=devolucao,
                item_pedido=item,
                quantidade=qtd,
                valor_unitario=item.valor_unitario,
                valor_total=qtd * item.valor_unitario,
                retornar_ao_estoque=retornar,
            )
            valor_total += qtd * item.valor_unitario

            if retornar:
                cls._registrar_retorno_estoque(
                    pedido=pedido,
                    item=item,
                    quantidade=qtd,
                    usuario=usuario,
                    devolucao=devolucao,
                    motivo=motivo,
                )

        devolucao.valor_total = valor_total
        devolucao.status = DevolucaoVenda.Status.FINALIZADA
        devolucao.save(update_fields=['valor_total', 'status', 'updated_at'])

        # Reduz saldo devedor do cliente
        pedido.cliente.saldo_devedor = F('saldo_devedor') - valor_total
        pedido.cliente.save(update_fields=['saldo_devedor', 'updated_at'])

        return devolucao

    @classmethod
    def _registrar_retorno_estoque(
        cls,
        pedido: PedidoVenda,
        item: ItemPedidoVenda,
        quantidade: Decimal,
        usuario,
        devolucao: DevolucaoVenda,
        motivo: str,
    ):
        observacao = f'Devolucao {devolucao.numero}: {motivo}'
        if not item.produto.controla_lote:
            MovimentacaoService.registrar_movimentacao(
                produto_id=item.produto_id,
                filial_id=pedido.filial_id,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
                quantidade=quantidade,
                usuario_id=usuario.pk,
                valor_unitario=item.valor_unitario,
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.PEDIDO_VENDA,
                documento_id=pedido.pk,
                documento_numero=pedido.numero_pedido,
                observacao=observacao,
            )
            return

        restante = quantidade
        saidas = (
            MovimentacaoEstoque.objects
            .filter(
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.PEDIDO_VENDA,
                documento_id=pedido.pk,
                produto_id=item.produto_id,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
                lote_id__isnull=False,
            )
            .order_by('data_movimentacao', 'pk')
        )
        for saida in saidas:
            if restante <= 0:
                break
            quantidade_lote = min(restante, saida.quantidade)
            MovimentacaoService.registrar_movimentacao(
                produto_id=item.produto_id,
                filial_id=pedido.filial_id,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
                quantidade=quantidade_lote,
                usuario_id=usuario.pk,
                lote_id=saida.lote_id,
                valor_unitario=item.valor_unitario,
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.PEDIDO_VENDA,
                documento_id=pedido.pk,
                documento_numero=pedido.numero_pedido,
                observacao=observacao,
            )
            restante -= quantidade_lote

        if restante > 0:
            raise DadosInvalidosError(
                f'Nao foi possivel identificar lote para devolver o item {item.numero_item}.'
            )

    @staticmethod
    def _gerar_numero_devolucao(filial) -> str:
        ultima = DevolucaoVenda.objects.filter(filial=filial).order_by('-pk').first()
        if not ultima or not ultima.numero.startswith('DEV-'):
            return 'DEV-0000001'
        try:
            num = int(ultima.numero.split('-')[1]) + 1
        except (ValueError, IndexError):
            num = 1
        return f'DEV-{num:07d}'
