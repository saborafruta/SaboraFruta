"""Estorno seguro de entradas de NF ja efetivadas."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import F
from django.utils import timezone

from apps.core.tenant_context import tenant_atomic
from apps.compras.models import EntradaNF, EntradaNFParcela
from apps.compras.services.entrada_financeiro_service import DOCUMENTO_TIPO_ENTRADA_NF
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import Estoque, LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import StatusContaPagar
from apps.financeiro.models import ContaPagar
from apps.produtos.models import Produto


@dataclass
class ImpactoEstornoEntrada:
    entrada: EntradaNF
    movimentos_originais: list
    movimentos_estorno: list
    contas_pagar: list
    parcelas: list
    bloqueios: list[str]
    avisos: list[str]

    @property
    def pode_estornar(self):
        return not self.bloqueios

    @property
    def quantidade_total(self):
        total = sum((mov.quantidade for mov in self.movimentos_originais), Decimal('0'))
        return total

    @property
    def custo_total(self):
        total = sum((mov.valor_total or Decimal('0') for mov in self.movimentos_originais), Decimal('0'))
        return total


def _movimentos_originais(entrada: EntradaNF):
    return list(
        MovimentacaoEstoque.objects
        .filter(
            filial=entrada.filial,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.NFE,
            documento_id=entrada.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
        )
        .select_related('produto', 'lote', 'filial', 'usuario')
        .order_by('data_movimentacao', 'pk')
    )


def _movimentos_estorno(entrada: EntradaNF):
    return list(
        MovimentacaoEstoque.objects
        .filter(
            filial=entrada.filial,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.ESTORNO_ENTRADA,
            documento_id=entrada.pk,
        )
        .select_related('produto', 'lote', 'filial', 'usuario')
        .order_by('data_movimentacao', 'pk')
    )


def calcular_impacto_estorno_entrada(entrada: EntradaNF) -> ImpactoEstornoEntrada:
    entrada = (
        EntradaNF.objects
        .select_related('filial', 'fornecedor')
        .get(pk=entrada.pk)
    )
    movimentos = _movimentos_originais(entrada)
    estornos = _movimentos_estorno(entrada)
    contas = list(
        ContaPagar.objects
        .filter(
            filial=entrada.filial,
            documento_tipo=DOCUMENTO_TIPO_ENTRADA_NF,
            documento_id=entrada.pk,
        )
        .order_by('parcela', 'pk')
    )
    parcelas = list(entrada.parcelas_financeiras.all().order_by('data_vencimento', 'numero', 'pk'))
    bloqueios = []
    avisos = []

    if entrada.status != EntradaNF.Status.EFETIVADA:
        bloqueios.append('Somente entrada efetivada pode ser estornada.')
    if estornos:
        bloqueios.append('Esta entrada ja possui movimentos de estorno.')
    if not movimentos:
        bloqueios.append('Nao ha movimentos originais de entrada para estornar.')

    for mov in movimentos:
        posteriores = (
            MovimentacaoEstoque.objects
            .filter(
                filial=mov.filial,
                produto=mov.produto,
                data_movimentacao__gt=mov.data_movimentacao,
            )
            .exclude(documento_tipo=MovimentacaoEstoque.DocumentoTipo.NFE, documento_id=entrada.pk)
            .exclude(documento_tipo=MovimentacaoEstoque.DocumentoTipo.ESTORNO_ENTRADA, documento_id=entrada.pk)
        )
        if posteriores.exists():
            bloqueios.append(
                f'Produto {mov.produto.descricao}: existem movimentacoes posteriores. '
                'Estorno automatico bloqueado para preservar custo medio e rastreio.'
            )

        estoque = Estoque.objects.filter(
            filial=mov.filial, produto=mov.produto, deposito_id=mov.deposito_id,
        ).first()
        if not estoque or estoque.quantidade_atual < mov.quantidade:
            atual = estoque.quantidade_atual if estoque else Decimal('0')
            bloqueios.append(
                f'Produto {mov.produto.descricao}: saldo atual {atual} menor que o estorno {mov.quantidade}.'
            )

        if mov.lote_id:
            lote = LoteProduto.objects.filter(pk=mov.lote_id).first()
            if not lote or lote.quantidade_atual < mov.quantidade:
                atual_lote = lote.quantidade_atual if lote else Decimal('0')
                bloqueios.append(
                    f'Lote {mov.lote.numero_lote}: saldo atual {atual_lote} menor que o estorno {mov.quantidade}.'
                )
            elif lote.quantidade_atual != mov.quantidade:
                bloqueios.append(
                    f'Lote {mov.lote.numero_lote}: ja houve consumo parcial. Estorno automatico bloqueado.'
                )

    for conta in contas:
        if conta.status == StatusContaPagar.PAGO or conta.valor_pago > 0:
            bloqueios.append(
                f'Conta a pagar #{conta.pk} ja possui pagamento e precisa de tratamento financeiro manual.'
            )
        elif conta.status != StatusContaPagar.CANCELADO:
            avisos.append(f'Conta a pagar #{conta.pk} sera cancelada.')

    if parcelas:
        avisos.append(f'{len(parcelas)} parcela(s) financeira(s) serao marcadas como canceladas.')

    return ImpactoEstornoEntrada(
        entrada=entrada,
        movimentos_originais=movimentos,
        movimentos_estorno=estornos,
        contas_pagar=contas,
        parcelas=parcelas,
        bloqueios=bloqueios,
        avisos=avisos,
    )


@tenant_atomic
def estornar_entrada(entrada: EntradaNF, usuario, motivo: str) -> tuple[EntradaNF, list[MovimentacaoEstoque]]:
    if not motivo.strip():
        raise DadosInvalidosError('Informe a justificativa para estornar a entrada.')

    entrada = (
        EntradaNF.objects
        .select_for_update()
        .select_related('filial', 'fornecedor', 'pedido_compra')
        .get(pk=entrada.pk)
    )
    impacto = calcular_impacto_estorno_entrada(entrada)
    if impacto.bloqueios:
        raise DadosInvalidosError(' '.join(impacto.bloqueios))

    movimentos_estorno = []
    for mov in impacto.movimentos_originais:
        reverso = MovimentacaoService.registrar_movimentacao(
            produto_id=mov.produto_id,
            filial_id=mov.filial_id,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
            quantidade=mov.quantidade,
            usuario_id=usuario.pk,
            lote_id=mov.lote_id,
            valor_unitario=mov.valor_unitario,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.ESTORNO_ENTRADA,
            documento_id=entrada.pk,
            documento_numero=entrada.numero_nf,
            observacao=f'Estorno da entrada NF {entrada.numero_nf}/{entrada.serie_nf}. {motivo}',
            deposito_id=mov.deposito_id,
        )
        movimentos_estorno.append(reverso)
        estoque = Estoque.objects.select_for_update().get(
            filial_id=mov.filial_id, produto_id=mov.produto_id,
            deposito_id=mov.deposito_id,
        )
        if estoque.quantidade_atual <= 0:
            estoque.custo_medio = Decimal('0')
            estoque.save(update_fields=['custo_medio', 'updated_at'])
            Produto.objects.filter(pk=mov.produto_id).update(preco_custo_medio=Decimal('0'))

    for item in entrada.itens.select_related('item_pedido_compra').all():
        if item.item_pedido_compra_id:
            recebido = item.quantidade_recebida or item.quantidade_estoque or item.quantidade
            item_pedido = item.item_pedido_compra
            item_pedido.quantidade_recebida = max(Decimal('0'), item_pedido.quantidade_recebida - recebido)
            item_pedido.save(update_fields=['quantidade_recebida', 'updated_at'])

    for conta in impacto.contas_pagar:
        if conta.status != StatusContaPagar.CANCELADO:
            conta.status = StatusContaPagar.CANCELADO
            conta.observacao = f'{conta.observacao}\n[ESTORNADA]: {motivo}'.strip()
            conta.save(update_fields=['status', 'observacao', 'updated_at'])

    EntradaNFParcela.objects.filter(entrada=entrada).exclude(
        status=EntradaNFParcela.Status.CANCELADA,
    ).update(status=EntradaNFParcela.Status.CANCELADA, updated_at=timezone.now())

    entrada.status = EntradaNF.Status.ESTORNADA
    entrada.usuario_estorno = usuario
    entrada.data_estorno = timezone.now()
    entrada.observacao = f'{entrada.observacao}\n[ESTORNADA]: {motivo}'.strip()
    entrada.save(update_fields=['status', 'usuario_estorno', 'data_estorno', 'observacao', 'updated_at'])

    if entrada.pedido_compra_id:
        from apps.compras.services.compra_service import CompraService
        CompraService._atualizar_status_pedido(entrada.pedido_compra)

    return entrada, movimentos_estorno
