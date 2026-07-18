"""
MovimentacaoService — o service mais crítico do sistema.

Regras absolutas:
1. Toda alteração de Estoque.quantidade_atual passa por aqui (NUNCA escrever diretamente).
2. Seleção de lote respeita FEFO (First Expired First Out) automaticamente.
3. Lote vencido é INVISÍVEL para venda (filtro obrigatório no queryset).
4. Entradas recalculam custo médio ponderado.
5. Transferências entre filiais são atômicas e bilaterais.
6. Toda operação gera MovimentacaoEstoque com snapshots anterior/posterior.
7. Uso de SELECT FOR UPDATE para evitar race conditions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.core.services.exceptions import (
    DadosInvalidosError, EstoqueInsuficienteError, LoteVencidoError,
)
from apps.estoque.models import Estoque, LoteProduto, MovimentacaoEstoque


@dataclass
class ConsumoLote:
    """Representa o consumo de um lote específico numa saída FEFO."""
    lote_id: int
    numero_lote: str
    quantidade: Decimal
    custo_unitario: Decimal
    data_validade: datetime | None


class MovimentacaoService:
    """Serviço único para todas as operações de estoque."""

    # ----------------------------------------------------------------------
    # Seleção FEFO
    # ----------------------------------------------------------------------

    @staticmethod
    def selecionar_lotes_fifo(
        produto_id: int,
        filial_id: int,
        quantidade: Decimal,
    ) -> list[ConsumoLote]:
        """
        Retorna lotes a consumir respeitando FEFO. Exclui lotes vencidos,
        bloqueados, esgotados ou com quantidade zero.

        Levanta EstoqueInsuficienteError se soma dos lotes disponíveis < quantidade.
        """
        hoje = timezone.now().date()

        qs = LoteProduto.objects.filter(
            produto_id=produto_id,
            filial_id=filial_id,
            status=LoteProduto.Status.ATIVO,
            quantidade_atual__gt=0,
        ).filter(
            # Lotes sem validade ou com validade futura
            models_Q(data_validade__isnull=True) | models_Q(data_validade__gte=hoje)
        ).order_by(
            # FEFO: nulls_last para lotes sem validade ficarem por último
            F('data_validade').asc(nulls_last=True), 'created_at',
        )

        restante = quantidade
        consumos: list[ConsumoLote] = []

        for lote in qs:
            if restante <= 0:
                break
            consumir = min(lote.quantidade_atual, restante)
            consumos.append(ConsumoLote(
                lote_id=lote.pk,
                numero_lote=lote.numero_lote,
                quantidade=consumir,
                custo_unitario=lote.custo_unitario,
                data_validade=lote.data_validade,
            ))
            restante -= consumir

        if restante > 0:
            total_disponivel = quantidade - restante
            raise EstoqueInsuficienteError(
                f'Estoque insuficiente. Solicitado: {quantidade}, '
                f'disponível em lotes vigentes: {total_disponivel}.'
            )

        return consumos

    # ----------------------------------------------------------------------
    # Registro de movimentação
    # ----------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def registrar_movimentacao(
        cls,
        produto_id: int,
        filial_id: int,
        tipo_operacao: str,
        quantidade: Decimal,
        usuario_id: int,
        lote_id: int | None = None,
        valor_unitario: Decimal | None = None,
        documento_tipo: str = '',
        documento_id: int | None = None,
        documento_numero: str = '',
        observacao: str = '',
        filial_destino_id: int | None = None,
        forcar_estoque_negativo: bool = False,
        permitir_sem_lote: bool = False,
    ) -> MovimentacaoEstoque:
        """
        Registra UMA movimentação de estoque atomicamente.
        Usa SELECT FOR UPDATE para evitar race conditions.

        Para SAÍDAS com múltiplos lotes, chame múltiplas vezes (uma por lote).
        Para SAÍDAS simples: use `registrar_saida()` que encapsula FEFO.
        """
        from apps.produtos.models import Produto

        if quantidade <= 0:
            raise DadosInvalidosError('Quantidade deve ser positiva.')

        if (
            cls._produto_controla_lote(produto_id)
            and not lote_id
            and not forcar_estoque_negativo
            and not permitir_sem_lote
        ):
            raise DadosInvalidosError(
                'Produto controla lote; informe o lote para movimentar estoque.'
            )

        cls._validar_bloqueio_inventario(filial_id, documento_tipo)

        # Lock pessimista na linha de estoque
        estoque, created = Estoque.objects.select_for_update().get_or_create(
            produto_id=produto_id, filial_id=filial_id,
            defaults={'quantidade_atual': 0, 'quantidade_reservada': 0,
                      'quantidade_disponivel': 0},
        )

        qtd_anterior = estoque.quantidade_atual
        custo_anterior = estoque.custo_medio

        # Aplica a movimentação
        eh_entrada = cls._eh_entrada(tipo_operacao)
        if eh_entrada:
            nova_qtd = qtd_anterior + quantidade
            # Recalcular custo médio ponderado se entrada com valor
            novo_custo = cls._recalcular_custo_medio(
                qtd_anterior, custo_anterior, quantidade,
                valor_unitario or Decimal('0'),
            )
        else:
            # Verificação de saldo — sempre permite estoque negativo neste nível.
            # A decisão de bloquear ou permitir fica em registrar_saida_fefo.
            nova_qtd = qtd_anterior - quantidade
            novo_custo = custo_anterior  # saídas não alteram custo médio

        # Se tem lote, atualizar também
        lote = None
        if lote_id:
            try:
                lote = LoteProduto.objects.select_for_update().get(
                    pk=lote_id,
                    produto_id=produto_id,
                    filial_id=filial_id,
                )
            except LoteProduto.DoesNotExist:
                raise DadosInvalidosError(
                    'Lote informado nao pertence ao produto/filial da movimentacao.'
                )
            if not eh_entrada and not cls._permite_saida_lote(lote, tipo_operacao):
                if lote.esta_vencido:
                    raise LoteVencidoError(
                        f'Lote {lote.numero_lote} está vencido desde {lote.data_validade:%d/%m/%Y}.'
                    )
                raise DadosInvalidosError(
                    f'Lote {lote.numero_lote} nao esta disponivel para saida '
                    f'({lote.get_status_display()}).'
                )
            if eh_entrada:
                lote.quantidade_atual = F('quantidade_atual') + quantidade
            else:
                if lote.quantidade_atual < quantidade and not forcar_estoque_negativo:
                    raise EstoqueInsuficienteError(
                        f'Lote {lote.numero_lote}: solicitado {quantidade}, '
                        f'disponível {lote.quantidade_atual}.'
                    )
                lote.quantidade_atual = F('quantidade_atual') - quantidade
            lote.save(update_fields=['quantidade_atual', 'updated_at'])
            lote.refresh_from_db()
            if lote.quantidade_atual <= 0 and lote.status == LoteProduto.Status.ATIVO:
                lote.status = LoteProduto.Status.ESGOTADO
                lote.save(update_fields=['status', 'updated_at'])
            elif (
                eh_entrada
                and lote.quantidade_atual > 0
                and lote.status == LoteProduto.Status.ESGOTADO
                and not lote.esta_vencido
            ):
                lote.status = LoteProduto.Status.ATIVO
                lote.save(update_fields=['status', 'updated_at'])
            from apps.estoque.services.alerta_service import AlertaService
            AlertaService.gerar_alertas_lote(lote)

        # Atualiza estoque
        estoque.quantidade_atual = nova_qtd
        estoque.atualizar_disponivel()
        estoque.custo_medio = novo_custo
        if eh_entrada:
            estoque.ultima_entrada = timezone.now()
        else:
            estoque.ultima_saida = timezone.now()
        estoque.save()

        # Atualiza custo médio no Produto também (denormalizado para consultas rápidas)
        if eh_entrada and valor_unitario:
            Produto.objects.filter(pk=produto_id).update(
                preco_custo_medio=novo_custo,
            )

        # Grava movimentação
        mov = MovimentacaoEstoque.objects.create(
            produto_id=produto_id,
            filial_id=filial_id,
            lote=lote,
            tipo_operacao=tipo_operacao,
            documento_tipo=documento_tipo,
            documento_id=documento_id,
            documento_numero=documento_numero,
            quantidade=quantidade,
            quantidade_anterior=qtd_anterior,
            quantidade_posterior=nova_qtd,
            valor_unitario=valor_unitario,
            valor_total=(valor_unitario * quantidade) if valor_unitario else None,
            custo_medio_anterior=custo_anterior,
            custo_medio_posterior=novo_custo,
            usuario_id=usuario_id,
            filial_destino_id=filial_destino_id,
            observacao=observacao,
            data_movimentacao=timezone.now(),
        )
        return mov

    @classmethod
    @transaction.atomic
    def registrar_saida_fefo(
        cls,
        produto_id: int,
        filial_id: int,
        quantidade: Decimal,
        usuario_id: int,
        tipo_operacao: str = MovimentacaoEstoque.TipoOperacao.SAIDA,
        documento_tipo: str = '',
        documento_id: int | None = None,
        documento_numero: str = '',
        forcar_estoque_negativo: bool = False,
    ) -> list[MovimentacaoEstoque]:
        """
        Saída automática respeitando FEFO.
        Pode gerar múltiplas movimentações (uma por lote consumido).
        Quando forcar_estoque_negativo=True, ignora a verificação de saldo
        e permite que o estoque fique negativo (venda autorizada pelo operador).
        """
        controla_lote = cls._produto_controla_lote(produto_id)
        if not controla_lote or forcar_estoque_negativo:
            # Verificar saldo apenas se NÃO estiver forçando
            if not forcar_estoque_negativo:
                from apps.estoque.models import Estoque
                estoque_atual = Estoque.objects.filter(
                    produto_id=produto_id, filial_id=filial_id
                ).values_list('quantidade_atual', flat=True).first() or 0
                if estoque_atual < quantidade:
                    raise EstoqueInsuficienteError(
                        f'Estoque insuficiente. Atual: {estoque_atual}, solicitado: {quantidade}.'
                    )
            return [
                cls.registrar_movimentacao(
                    produto_id=produto_id,
                    filial_id=filial_id,
                    tipo_operacao=tipo_operacao,
                    quantidade=quantidade,
                    usuario_id=usuario_id,
                    documento_tipo=documento_tipo,
                    documento_id=documento_id,
                    documento_numero=documento_numero,
                    observacao='Saida sem controle de lote.' if not forcar_estoque_negativo else 'Venda com estoque negativo autorizada pelo operador.',
                    forcar_estoque_negativo=forcar_estoque_negativo,
                )
            ]

        consumos = cls.selecionar_lotes_fifo(produto_id, filial_id, quantidade)
        movimentacoes = []
        for c in consumos:
            mov = cls.registrar_movimentacao(
                produto_id=produto_id,
                filial_id=filial_id,
                tipo_operacao=tipo_operacao,
                quantidade=c.quantidade,
                usuario_id=usuario_id,
                lote_id=c.lote_id,
                valor_unitario=c.custo_unitario,
                documento_tipo=documento_tipo,
                documento_id=documento_id,
                documento_numero=documento_numero,
                observacao=f'FEFO: lote {c.numero_lote}',
            )
            movimentacoes.append(mov)
        return movimentacoes

    # ----------------------------------------------------------------------
    # Transferência bilateral entre filiais
    # ----------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def transferir_entre_filiais(
        cls,
        produto_id: int,
        filial_origem_id: int,
        filial_destino_id: int,
        quantidade: Decimal,
        usuario_id: int,
        lote_id: int | None = None,
        observacao: str = '',
        permitir_sem_lote: bool = False,
    ) -> tuple[MovimentacaoEstoque, MovimentacaoEstoque]:
        """
        Cria DUAS movimentações (saída na origem + entrada no destino).
        Ambas em uma única transação — se uma falhar, a outra é revertida.
        """
        if filial_origem_id == filial_destino_id:
            raise DadosInvalidosError('Filial de origem e destino não podem ser iguais.')

        cls._validar_produto_transferivel(produto_id, filial_origem_id, filial_destino_id)

        custo_unitario = None
        lote_origem = None
        if lote_id:
            try:
                lote_origem = LoteProduto.objects.get(
                    pk=lote_id,
                    produto_id=produto_id,
                    filial_id=filial_origem_id,
                )
            except LoteProduto.DoesNotExist:
                raise DadosInvalidosError(
                    'Lote informado nao pertence ao produto/filial de origem.'
                )
            custo_unitario = lote_origem.custo_unitario

        # Saída na origem
        mov_saida = cls.registrar_movimentacao(
            produto_id=produto_id,
            filial_id=filial_origem_id,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
            quantidade=quantidade,
            usuario_id=usuario_id,
            lote_id=lote_id,
            valor_unitario=custo_unitario,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.TRANSFERENCIA,
            observacao=observacao or f'Transferência para filial {filial_destino_id}',
            filial_destino_id=filial_destino_id,
            permitir_sem_lote=permitir_sem_lote,
        )
        documento_numero = f'TRF-{mov_saida.pk:06d}'

        # Entrada no destino
        # O lote precisa ser clonado ou transportado para a filial destino.
        # Estratégia: criar novo registro de lote no destino (mesmo número).
        lote_destino_id = None
        if lote_origem:
            lote_destino, _ = LoteProduto.objects.get_or_create(
                produto_id=produto_id,
                filial_id=filial_destino_id,
                numero_lote=lote_origem.numero_lote,
                defaults={
                    'data_fabricacao': lote_origem.data_fabricacao,
                    'data_validade': lote_origem.data_validade,
                    'fornecedor': lote_origem.fornecedor,
                    'custo_unitario': lote_origem.custo_unitario,
                    'quantidade_inicial': quantidade,
                    'quantidade_atual': 0,
                },
            )
            lote_destino_id = lote_destino.pk

        mov_entrada = cls.registrar_movimentacao(
            produto_id=produto_id,
            filial_id=filial_destino_id,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_ENTRADA,
            quantidade=quantidade,
            usuario_id=usuario_id,
            lote_id=lote_destino_id,
            valor_unitario=custo_unitario,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.TRANSFERENCIA,
            documento_id=mov_saida.pk,
            documento_numero=documento_numero,
            observacao=f'Recebido de filial {filial_origem_id} — mov. saída #{mov_saida.pk}',
            permitir_sem_lote=permitir_sem_lote,
        )

        mov_saida.documento_id = mov_entrada.pk
        mov_saida.documento_numero = documento_numero
        mov_saida.save(update_fields=['documento_id', 'documento_numero'])

        return mov_saida, mov_entrada

    # ----------------------------------------------------------------------
    # Entrada por compra (com lote)
    # ----------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def registrar_entrada_compra(
        cls,
        produto_id: int,
        filial_id: int,
        quantidade: Decimal,
        valor_unitario: Decimal,
        usuario_id: int,
        fornecedor_id: int | None = None,
        numero_lote: str = '',
        data_fabricacao=None,
        data_validade=None,
        numero_nota: str = '',
        documento_id: int | None = None,
    ) -> MovimentacaoEstoque:
        """Entrada por compra: cria lote (se aplicável) + movimentação + atualiza custo médio."""
        lote_id = None
        if numero_lote:
            lote, created = LoteProduto.objects.select_for_update().get_or_create(
                produto_id=produto_id,
                filial_id=filial_id,
                numero_lote=numero_lote,
                defaults={
                    'data_fabricacao': data_fabricacao,
                    'data_validade': data_validade,
                    'fornecedor_id': fornecedor_id,
                    'numero_nota_entrada': numero_nota,
                    'custo_unitario': valor_unitario,
                    'quantidade_inicial': quantidade,
                    'quantidade_atual': 0,  # incrementado pela movimentação
                },
            )
            if not created and valor_unitario:
                lote.custo_unitario = cls._recalcular_custo_medio(
                    lote.quantidade_atual,
                    lote.custo_unitario,
                    quantidade,
                    valor_unitario,
                )
                if data_fabricacao and not lote.data_fabricacao:
                    lote.data_fabricacao = data_fabricacao
                if data_validade and not lote.data_validade:
                    lote.data_validade = data_validade
                lote.save(update_fields=[
                    'custo_unitario',
                    'data_fabricacao',
                    'data_validade',
                    'updated_at',
                ])
            lote_id = lote.pk

        return cls.registrar_movimentacao(
            produto_id=produto_id,
            filial_id=filial_id,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=quantidade,
            usuario_id=usuario_id,
            lote_id=lote_id,
            valor_unitario=valor_unitario,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.NFE,
            documento_id=documento_id,
            documento_numero=numero_nota,
            observacao=f'Entrada por compra - NF {numero_nota}' if numero_nota else '',
        )

    # ----------------------------------------------------------------------
    # Ajuste manual (inventário)
    # ----------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def ajustar_manual(
        cls,
        produto_id: int,
        filial_id: int,
        quantidade_nova: Decimal,
        usuario_id: int,
        justificativa: str,
        lote_id: int | None = None,
        documento_tipo: str = MovimentacaoEstoque.DocumentoTipo.AJUSTE_MANUAL,
        documento_id: int | None = None,
        documento_numero: str = '',
    ) -> MovimentacaoEstoque:
        """Define a quantidade como X (faz ajuste para mais ou menos)."""
        if not justificativa.strip():
            raise DadosInvalidosError('Ajuste manual requer justificativa.')

        if lote_id:
            lote = LoteProduto.objects.select_for_update().get(
                pk=lote_id,
                produto_id=produto_id,
                filial_id=filial_id,
            )
            diferenca = quantidade_nova - lote.quantidade_atual
            if diferenca == 0:
                raise DadosInvalidosError('Quantidade atual ja e igual a informada.')
            tipo = (
                MovimentacaoEstoque.TipoOperacao.AJUSTE_MAIS if diferenca > 0
                else MovimentacaoEstoque.TipoOperacao.AJUSTE_MENOS
            )
            return cls.registrar_movimentacao(
                produto_id=produto_id,
                filial_id=filial_id,
                tipo_operacao=tipo,
                quantidade=abs(diferenca),
                usuario_id=usuario_id,
                lote_id=lote_id,
                documento_tipo=documento_tipo,
                documento_id=documento_id,
                documento_numero=documento_numero,
                observacao=justificativa,
            )

        estoque, _ = Estoque.objects.select_for_update().get_or_create(
            produto_id=produto_id, filial_id=filial_id,
        )
        diferenca = quantidade_nova - estoque.quantidade_atual
        if diferenca == 0:
            raise DadosInvalidosError('Quantidade atual já é igual à informada.')

        tipo = (
            MovimentacaoEstoque.TipoOperacao.AJUSTE_MAIS if diferenca > 0
            else MovimentacaoEstoque.TipoOperacao.AJUSTE_MENOS
        )
        return cls.registrar_movimentacao(
            produto_id=produto_id,
            filial_id=filial_id,
            tipo_operacao=tipo,
            quantidade=abs(diferenca),
            usuario_id=usuario_id,
            lote_id=lote_id,
            documento_tipo=documento_tipo,
            documento_id=documento_id,
            documento_numero=documento_numero,
            observacao=justificativa,
        )

    @classmethod
    @transaction.atomic
    def baixar_lote_por_validade(
        cls,
        lote_id: int,
        usuario_id: int,
        observacao: str = '',
    ) -> MovimentacaoEstoque:
        """Baixa todo saldo de um lote vencido com movimentacao auditada."""
        lote = (
            LoteProduto.objects
            .select_for_update()
            .select_related('produto')
            .get(pk=lote_id)
        )
        if lote.quantidade_atual <= 0:
            raise DadosInvalidosError('Lote sem quantidade para baixar.')
        if not lote.esta_vencido and lote.status != LoteProduto.Status.VENCIDO:
            raise DadosInvalidosError('Somente lote vencido pode ser baixado por validade.')

        if lote.status != LoteProduto.Status.VENCIDO:
            lote.status = LoteProduto.Status.VENCIDO
            lote.motivo_bloqueio = (
                f'Baixa por validade. Vencimento em {lote.data_validade:%d/%m/%Y}.'
                if lote.data_validade
                else 'Baixa por validade.'
            )
            lote.save(update_fields=['status', 'motivo_bloqueio', 'updated_at'])

        mov = cls.registrar_movimentacao(
            produto_id=lote.produto_id,
            filial_id=lote.filial_id,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.BAIXA_VALIDADE,
            quantidade=lote.quantidade_atual,
            usuario_id=usuario_id,
            lote_id=lote.pk,
            valor_unitario=lote.custo_unitario,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
            observacao=observacao or f'Baixa por validade do lote {lote.numero_lote}.',
        )

        from apps.estoque.services.alerta_service import AlertaService
        AlertaService.resolver_alertas_lote(lote)
        return mov

    # ----------------------------------------------------------------------
    # Reservas
    # ----------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def reservar_estoque(
        cls,
        produto_id: int,
        filial_id: int,
        quantidade: Decimal,
        permitir_sem_estoque: bool = False,
    ) -> Estoque:
        """Reserva quantidade sem baixar saldo fisico."""
        if quantidade <= 0:
            raise DadosInvalidosError('Quantidade deve ser positiva.')

        cls._validar_bloqueio_inventario(filial_id, '')

        controla_lote = cls._produto_controla_lote(produto_id)
        if controla_lote and not permitir_sem_estoque:
            cls.selecionar_lotes_fifo(produto_id, filial_id, quantidade)

        estoque, _ = Estoque.objects.select_for_update().get_or_create(
            produto_id=produto_id,
            filial_id=filial_id,
            defaults={
                'quantidade_atual': 0,
                'quantidade_reservada': 0,
                'quantidade_disponivel': 0,
            },
        )
        if estoque.quantidade_disponivel < quantidade and not permitir_sem_estoque:
            raise EstoqueInsuficienteError(
                f'Estoque insuficiente. Disponivel: {estoque.quantidade_disponivel}, '
                f'solicitado: {quantidade}.'
            )

        estoque.quantidade_reservada += quantidade
        estoque.atualizar_disponivel()
        estoque.save(update_fields=[
            'quantidade_reservada',
            'quantidade_disponivel',
            'updated_at',
        ])
        return estoque

    @classmethod
    @transaction.atomic
    def liberar_reserva(
        cls,
        produto_id: int,
        filial_id: int,
        quantidade: Decimal,
        tolerar_ausente: bool = False,
    ) -> Estoque | None:
        """Libera quantidade reservada sem alterar saldo fisico."""
        if quantidade <= 0:
            raise DadosInvalidosError('Quantidade deve ser positiva.')

        try:
            estoque = Estoque.objects.select_for_update().get(
                produto_id=produto_id,
                filial_id=filial_id,
            )
        except Estoque.DoesNotExist:
            if tolerar_ausente:
                return None
            raise DadosInvalidosError('Reserva de estoque nao encontrada.')

        if estoque.quantidade_reservada < quantidade:
            if not tolerar_ausente:
                raise DadosInvalidosError(
                    f'Reserva insuficiente. Reservado: {estoque.quantidade_reservada}, '
                    f'solicitado: {quantidade}.'
                )
            quantidade = estoque.quantidade_reservada

        estoque.quantidade_reservada -= quantidade
        estoque.atualizar_disponivel()
        estoque.save(update_fields=[
            'quantidade_reservada',
            'quantidade_disponivel',
            'updated_at',
        ])
        return estoque

    # ----------------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------------

    @staticmethod
    def _eh_entrada(tipo_operacao: str) -> bool:
        entradas = {
            MovimentacaoEstoque.TipoOperacao.ENTRADA,
            MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_ENTRADA,
            MovimentacaoEstoque.TipoOperacao.AJUSTE_MAIS,
            MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
            MovimentacaoEstoque.TipoOperacao.PRODUCAO_ENTRADA,
        }
        return tipo_operacao in entradas

    @staticmethod
    def _permite_saida_lote(lote: LoteProduto, tipo_operacao: str) -> bool:
        if tipo_operacao == MovimentacaoEstoque.TipoOperacao.BAIXA_VALIDADE:
            return lote.esta_vencido or lote.status == LoteProduto.Status.VENCIDO
        return lote.status == LoteProduto.Status.ATIVO and not lote.esta_vencido

    @staticmethod
    def _produto_controla_lote(produto_id: int) -> bool:
        from apps.produtos.models import Produto

        controla_lote = Produto.objects.filter(pk=produto_id).values_list(
            'controla_lote',
            flat=True,
        ).first()
        if controla_lote is None:
            raise DadosInvalidosError('Produto nao encontrado.')
        return bool(controla_lote)

    @staticmethod
    def _validar_produto_transferivel(
        produto_id: int,
        filial_origem_id: int,
        filial_destino_id: int,
    ) -> None:
        from apps.produtos.models import Produto, ProdutoFilial

        if not Produto.objects.filter(pk=produto_id).exists():
            raise DadosInvalidosError('Produto nao encontrado.')
        produto_vinculado_origem = ProdutoFilial.objects.filter(
            produto_id=produto_id,
            produto__ativo=True,
            filial_id=filial_origem_id,
            ativo=True,
        ).exists()
        if not produto_vinculado_origem:
            raise DadosInvalidosError('Produto nao esta ativo/vinculado a filial de origem.')
        produto_vinculado_destino = ProdutoFilial.objects.filter(
            produto_id=produto_id,
            produto__ativo=True,
            filial_id=filial_destino_id,
            ativo=True,
        ).exists()
        if not produto_vinculado_destino:
            raise DadosInvalidosError(
                'Produto nao esta ativo/vinculado a filial de destino. '
                'Vincule o produto antes de transferir estoque.'
            )

    @staticmethod
    def _validar_bloqueio_inventario(filial_id: int, documento_tipo: str) -> None:
        if documento_tipo == MovimentacaoEstoque.DocumentoTipo.INVENTARIO:
            return
        from apps.estoque.models import Inventario

        bloqueado = Inventario.objects.filter(
            filial_id=filial_id,
            bloquear_movimentacoes=True,
            status__in=[
                Inventario.Status.ABERTO,
                Inventario.Status.EM_CONTAGEM,
                Inventario.Status.EM_CONFERENCIA,
            ],
        ).exists()
        if bloqueado:
            raise DadosInvalidosError(
                'Existe inventario aberto bloqueando movimentacoes nesta filial.'
            )

    @staticmethod
    def _recalcular_custo_medio(
        qtd_atual: Decimal, custo_atual: Decimal,
        qtd_entrada: Decimal, custo_entrada: Decimal,
    ) -> Decimal:
        """Custo médio ponderado."""
        if custo_entrada == 0:
            return custo_atual
        total = qtd_atual + qtd_entrada
        if total <= 0:
            return custo_entrada
        novo = ((custo_atual * qtd_atual) + (custo_entrada * qtd_entrada)) / total
        return novo.quantize(Decimal('0.0001'))


# Import Q no escopo correto
from django.db.models import Q as models_Q  # noqa: E402
