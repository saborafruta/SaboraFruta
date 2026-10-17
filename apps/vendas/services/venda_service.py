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
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import F, Max
from django.utils import timezone

from apps.core.tenant_context import tenant_atomic
from apps.core.services.exceptions import (
    DadosInvalidosError, EstoqueInsuficienteError, PermissaoNegadaError,
)
from apps.estoque.models import Deposito, MovimentacaoEstoque
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
    @tenant_atomic
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
    @tenant_atomic
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
            quantidade_solicitada=quantidade,
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
    @tenant_atomic
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
    @tenant_atomic
    def aprovar_e_confirmar_pedido(cls, pedido: PedidoVenda, usuario) -> PedidoVenda:
        """Aprova um pedido externo e executa crédito + reserva na mesma transação."""
        if pedido.status != PedidoVenda.Status.AGUARDANDO_APROVACAO:
            raise DadosInvalidosError(
                f'Pedido não pode ser aprovado no status "{pedido.get_status_display()}".'
            )
        pedido.status = PedidoVenda.Status.APROVADO
        pedido.aprovado_por = usuario
        pedido.data_aprovacao = timezone.now()
        pedido.save(update_fields=[
            'status', 'aprovado_por', 'data_aprovacao', 'updated_at',
        ])
        return cls.confirmar_pedido(pedido, usuario)

    @classmethod
    @tenant_atomic
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
                    deposito_id=Deposito.venda_id(pedido.filial_id),
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
    @tenant_atomic
    def separar_pedido(
        cls, pedido: PedidoVenda, usuario, itens_conferidos: list[dict] | None = None,
        observacao: str = '',
    ) -> SeparacaoPedido:
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
            observacao=(observacao or '').strip(),
        )

        itens_originais = list(pedido.itens.select_related('produto').order_by('numero_item'))
        if pedido.origem == PedidoVenda.Origem.EXTERNO and itens_conferidos is None:
            raise DadosInvalidosError(
                'A separação de pedido externo exige a conferência de todos os itens.'
            )
        if itens_conferidos is None:
            itens_conferidos = [
                {
                    'item_id': item.pk,
                    'conferido': True,
                    'quantidade_separada': item.quantidade,
                }
                for item in itens_originais
            ]
        dados_por_item = {int(dado['item_id']): dado for dado in itens_conferidos}
        itens_finais = []
        proximo_numero = (
            pedido.itens.aggregate(maior=Max('numero_item'))['maior'] or 0
        ) + 1

        for item in itens_originais:
            dado = dados_por_item.get(item.pk)
            if not dado or not dado.get('conferido'):
                raise DadosInvalidosError(
                    f'Confirme a separação do item {item.numero_item}: {item.produto}.'
                )
            solicitada = item.quantidade_solicitada or item.quantidade
            try:
                separada = Decimal(str(dado.get('quantidade_separada', '0')))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise DadosInvalidosError(f'Quantidade inválida no item {item.numero_item}.') from exc
            if separada < 0 or separada > solicitada:
                raise DadosInvalidosError(
                    f'A quantidade separada do item {item.numero_item} deve ficar entre 0 e {solicitada}.'
                )
            observacao_item = (dado.get('observacao') or '').strip()
            substituto_id = dado.get('produto_substituto_id')
            try:
                quantidade_substituta = Decimal(str(dado.get('quantidade_substituta') or '0'))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise DadosInvalidosError(f'Quantidade substituta inválida no item {item.numero_item}.') from exc
            if separada < solicitada and not observacao_item and not (substituto_id and quantidade_substituta > 0):
                raise DadosInvalidosError(
                    f'Informe a divergência ou um substituto para o item {item.numero_item}.'
                )

            diferenca = item.quantidade - separada
            if diferenca > 0:
                MovimentacaoService.liberar_reserva(
                    produto_id=item.produto_id,
                    filial_id=pedido.filial_id,
                    quantidade=diferenca,
                    tolerar_ausente=True,
                    deposito_id=Deposito.venda_id(pedido.filial_id),
                )
            item.quantidade_solicitada = solicitada
            item.quantidade = separada
            item.quantidade_atendida = separada
            item.quantidade_reservada = separada
            item.separacao_conferida = True
            item.observacao_separacao = observacao_item
            item.calcular_totais()
            item.save(update_fields=[
                'quantidade_solicitada', 'quantidade', 'quantidade_atendida',
                'quantidade_reservada', 'separacao_conferida',
                'observacao_separacao', 'valor_bruto', 'valor_desconto',
                'desconto_percentual', 'desconto_valor', 'valor_total', 'updated_at',
            ])
            if separada > 0:
                itens_finais.append(item)

            if substituto_id and quantidade_substituta > 0:
                from apps.produtos.models import Produto

                substituto = (
                    Produto.objects.for_filial(pedido.filial)
                    .filter(pk=substituto_id, ativo=True)
                    .first()
                )
                if not substituto:
                    raise DadosInvalidosError(
                        f'O produto substituto do item {item.numero_item} não está disponível.'
                    )
                contrato = ProdutoVendavelService.validar_venda(
                    produto=substituto,
                    filial=pedido.filial,
                    quantidade=quantidade_substituta,
                    cliente=pedido.cliente,
                    tabela_preco=pedido.tabela_preco,
                )
                MovimentacaoService.reservar_estoque(
                    produto_id=substituto.pk,
                    filial_id=pedido.filial_id,
                    quantidade=quantidade_substituta,
                    permitir_sem_estoque=substituto.permite_venda_sem_estoque,
                    deposito_id=Deposito.venda_id(pedido.filial_id),
                )
                novo_item = ItemPedidoVenda(
                    pedido=pedido,
                    produto=substituto,
                    numero_item=proximo_numero,
                    quantidade=quantidade_substituta,
                    quantidade_solicitada=quantidade_substituta,
                    quantidade_atendida=quantidade_substituta,
                    quantidade_reservada=quantidade_substituta,
                    valor_unitario=contrato['preco_aplicado'],
                    custo_unitario_snapshot=contrato['custo_atual'],
                    separacao_conferida=True,
                    item_substituido=item,
                    observacao_separacao=f'Substitui o item {item.numero_item}. {observacao_item}'.strip(),
                )
                novo_item.calcular_totais()
                novo_item.save()
                proximo_numero += 1
                itens_finais.append(novo_item)

        pedido.recalcular_totais()
        pedido.save(update_fields=[
            'valor_produtos', 'valor_desconto', 'desconto_percentual',
            'desconto_valor', 'acrescimo', 'valor_total', 'updated_at',
        ])

        for item in itens_finais:
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
    @tenant_atomic
    def faturar_pedido(
        cls, pedido: PedidoVenda, usuario, forma_pagamento=None,
        condicao_pagamento=None, pagamento_confirmado: bool | None = None,
        prazo_pagamento_dias: int | None = None, referencia_pagamento: str = '',
    ) -> PedidoVenda:
        """
        Dá baixa real no estoque, libera reserva e muda para FATURADO.
        Se houver separação, usa os lotes identificados; senão, usa FEFO direto.
        """
        if not pedido.pode_faturar:
            raise DadosInvalidosError(
                f'Pedido não pode ser faturado no status "{pedido.get_status_display()}".'
            )

        if pedido.origem == PedidoVenda.Origem.EXTERNO and not (
            forma_pagamento or pedido.forma_pagamento_id
        ):
            raise DadosInvalidosError('Informe a forma de pagamento antes de faturar.')
        if forma_pagamento is not None:
            pedido.forma_pagamento = forma_pagamento
        if condicao_pagamento is not None:
            pedido.condicao_pagamento = condicao_pagamento
        if pagamento_confirmado is not None:
            pedido.pagamento_confirmado = pagamento_confirmado
        if prazo_pagamento_dias is not None:
            pedido.prazo_pagamento_dias = max(int(prazo_pagamento_dias), 0)
        if referencia_pagamento:
            pedido.referencia_pagamento = referencia_pagamento.strip()[:500]
        pedido.save(update_fields=[
            'forma_pagamento', 'condicao_pagamento', 'pagamento_confirmado',
            'prazo_pagamento_dias', 'referencia_pagamento', 'updated_at',
        ])

        separacao = pedido.separacoes.filter(
            status=SeparacaoPedido.Status.CONCLUIDA,
        ).order_by('-data_inicio').first()

        for item in pedido.itens.select_related('produto'):
            if (item.quantidade or Decimal('0')) <= Decimal('0'):
                item.quantidade_reservada = Decimal('0')
                item.quantidade_faturada = Decimal('0')
                item.save(update_fields=[
                    'quantidade_reservada', 'quantidade_faturada', 'updated_at',
                ])
                continue
            # Liberar reserva primeiro
            MovimentacaoService.liberar_reserva(
                produto_id=item.produto_id,
                filial_id=pedido.filial_id,
                quantidade=item.quantidade,
                deposito_id=Deposito.venda_id(pedido.filial_id),
            )

            # A MERCADORIA JA' EMBARCADA NAO BAIXA DE NOVO. Quando o pedido foi
            # carregado numa viagem que ja' fechou a carga, o estoque saiu la'
            # -- faturar depois tirava a mesma caixa uma segunda vez. A reserva
            # continua sendo liberada acima: ela e' do pedido, nao do
            # movimento. Ver `SaidaUnicaService`.
            from apps.logistica.services.saida_unica import SaidaUnicaService

            embarcado = SaidaUnicaService.embarcado_em_viagem(
                pedido.pk, item.produto_id,
            )
            restante = (item.quantidade or Decimal('0')) - embarcado
            if restante <= Decimal('0'):
                continue

            # Executar saída real
            if separacao and item.produto.controla_lote:
                itens_sep = separacao.itens.filter(item_pedido=item)
                # A PARTE JA' EMBARCADA SAI DA CONTA LOTE A LOTE: metade
                # carregada e metade faturada precisa somar UMA saida, e nao
                # uma e meia.
                falta = restante
                for sep in itens_sep:
                    if falta <= Decimal('0'):
                        break
                    quantidade_lote = min(sep.quantidade_separada, falta)
                    MovimentacaoService.registrar_movimentacao(
                        produto_id=item.produto_id,
                        filial_id=pedido.filial_id,
                        tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
                        quantidade=quantidade_lote,
                        usuario_id=usuario.pk,
                        lote_id=sep.lote_id,
                        valor_unitario=item.valor_unitario,
                        documento_tipo=MovimentacaoEstoque.DocumentoTipo.PEDIDO_VENDA,
                        documento_id=pedido.pk,
                        documento_numero=pedido.numero_pedido,
                        deposito_id=Deposito.venda_id(pedido.filial_id),
                    )
                    falta -= quantidade_lote
            else:
                # Produto sem controle de lote ou sem separação prévia → FEFO direto
                MovimentacaoService.registrar_saida_fefo(
                    produto_id=item.produto_id,
                    filial_id=pedido.filial_id,
                    quantidade=restante,
                    usuario_id=usuario.pk,
                    tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
                    documento_tipo=MovimentacaoEstoque.DocumentoTipo.PEDIDO_VENDA,
                    documento_id=pedido.pk,
                    documento_numero=pedido.numero_pedido,
                    deposito_id=Deposito.venda_id(pedido.filial_id),
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

        # Atualizar saldo devedor apenas quando a venda ficou a receber.
        if not pedido.pagamento_confirmado:
            pedido.cliente.saldo_devedor = F('saldo_devedor') + pedido.valor_total
            pedido.cliente.save(update_fields=['saldo_devedor', 'updated_at'])

        pedido.status = PedidoVenda.Status.FATURADO
        pedido.save(update_fields=['status', 'updated_at'])

        if pedido.forma_pagamento_id and not pedido.pagamento_confirmado:
            cls._gerar_contas_receber(pedido, usuario)

        # Atualiza o padrão de recompra do cliente (CRM). Falha aqui nunca
        # pode derrubar o faturamento — o serviço já engole a exceção.
        from apps.crm.services import RecompraService
        RecompraService.recalcular_cliente_da_venda(pedido.filial, pedido.cliente_id)

        return pedido

    @staticmethod
    def _gerar_contas_receber(pedido: PedidoVenda, usuario):
        """Gera títulos idempotentes conforme a condição comercial do pedido."""
        from apps.financeiro.models import ContaReceber
        from apps.financeiro.services.parcelamento import ParcelamentoService
        from apps.financeiro.services.receber_service import ContaReceberService

        documento_tipo = 'pedido_venda'
        if ContaReceber.objects.for_filial(pedido.filial).filter(
            documento_tipo=documento_tipo,
            documento_id=pedido.pk,
        ).exists():
            return

        emissao = timezone.localdate()
        parcelas = ParcelamentoService.parcelas(
            pedido.valor_total,
            emissao,
            condicao=pedido.condicao_pagamento,
            forma=pedido.forma_pagamento,
        )
        if pedido.prazo_pagamento_dias and parcelas:
            primeira_original = parcelas[0][0]
            primeira_desejada = emissao + timedelta(days=pedido.prazo_pagamento_dias)
            deslocamento = primeira_desejada - primeira_original
            parcelas = [(vencimento + deslocamento, valor) for vencimento, valor in parcelas]

        total = len(parcelas)
        for indice, (vencimento, valor) in enumerate(parcelas, start=1):
            conta = ContaReceberService.criar(
                filial=pedido.filial,
                cliente=pedido.cliente,
                valor_original=valor,
                data_emissao=emissao,
                data_vencimento=vencimento,
                parcela=indice,
                total_parcelas=total,
                documento_numero=pedido.numero_pedido,
                forma_pagamento=pedido.forma_pagamento,
                observacao=f'Gerado pelo faturamento do pedido {pedido.numero_pedido}.',
                usuario=usuario,
            )
            conta.documento_tipo = documento_tipo
            conta.documento_id = pedido.pk
            conta.save(update_fields=['documento_tipo', 'documento_id', 'updated_at'])

    @classmethod
    @tenant_atomic
    def iniciar_entrega(
        cls, pedido: PedidoVenda, usuario, responsavel: str = '', observacao: str = '',
    ) -> PedidoVenda:
        if not pedido.pode_iniciar_entrega:
            raise DadosInvalidosError(
                f'Pedido não pode sair para entrega no status "{pedido.get_status_display()}".'
            )
        pedido.status = PedidoVenda.Status.EM_ENTREGA
        pedido.data_saida_entrega = timezone.now()
        pedido.responsavel_entrega = (responsavel or '').strip()[:120]
        pedido.observacao_entrega = (observacao or '').strip()
        pedido.save(update_fields=[
            'status', 'data_saida_entrega', 'responsavel_entrega',
            'observacao_entrega', 'updated_at',
        ])
        return pedido

    @classmethod
    @tenant_atomic
    def concluir_entrega(cls, pedido: PedidoVenda) -> PedidoVenda:
        if pedido.status != PedidoVenda.Status.EM_ENTREGA:
            raise DadosInvalidosError('Apenas pedidos em entrega podem ser concluídos.')
        pedido.status = PedidoVenda.Status.ENTREGUE
        pedido.data_entrega_realizada = timezone.localdate()
        pedido.save(update_fields=['status', 'data_entrega_realizada', 'updated_at'])
        return pedido

    @classmethod
    @tenant_atomic
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
                    deposito_id=Deposito.venda_id(pedido.filial_id),
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
    @tenant_atomic
    def criar_devolucao(
        cls, pedido: PedidoVenda, usuario, motivo: str, itens_devolvidos: list[dict],
        descricao: str = '',
    ) -> DevolucaoVenda:
        """
        Cria devolução retornando produtos ao estoque.
        itens_devolvidos = [{'item_pedido_id': int, 'quantidade': Decimal,
        'retornar_ao_estoque': bool, 'apresentacao': ProdutoApresentacao|None}, ...]
        Se 'apresentacao' for informada, 'quantidade' e' na unidade dessa
        apresentacao (ex: 2 caixas) e e' convertida pra unidade base antes
        de validar contra item.quantidade.
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
            data_devolucao=timezone.localdate(),
            usuario=usuario,
            aprovado_por=usuario,
        )

        valor_total = Decimal('0')
        for dev in itens_devolvidos:
            item = ItemPedidoVenda.objects.get(
                pk=dev['item_pedido_id'], pedido=pedido,
            )
            qtd_informada = Decimal(str(dev['quantidade']))
            apresentacao = dev.get('apresentacao')
            quantidade_comercial = None
            if apresentacao is not None:
                if apresentacao.produto_id != item.produto_id:
                    raise DadosInvalidosError(
                        f'Apresentacao "{apresentacao.descricao}" nao pertence ao produto '
                        f'do item {item.numero_item}.'
                    )
                if not apresentacao.permite_venda:
                    raise DadosInvalidosError(
                        f'A apresentacao "{apresentacao.descricao}" nao pode ser usada em venda/devolucao.'
                    )
                quantidade_comercial = qtd_informada
                qtd = apresentacao.converter_para_base(qtd_informada)
            else:
                qtd = qtd_informada

            if qtd <= 0 or qtd > item.quantidade:
                raise DadosInvalidosError(
                    f'Quantidade inválida para item {item.numero_item}.'
                )

            retornar = dev.get('retornar_ao_estoque', True)
            ItemDevolucao.objects.create(
                devolucao=devolucao,
                item_pedido=item,
                quantidade=qtd,
                apresentacao=apresentacao,
                apresentacao_fator_conversao=apresentacao.fator_conversao if apresentacao else None,
                quantidade_comercial=quantidade_comercial,
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
