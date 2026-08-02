"""Servicos de pedido de compra e entrada de mercadoria."""
from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, F
from django.utils import timezone

from apps.compras.models import (
    AvaliacaoFornecedor, EntradaNF, ItemEntradaNF, ItemPedidoCompra, PedidoCompra,
)
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.compras.services.entrada_custo_service import EntradaCustoService

logger = logging.getLogger(__name__)


class CompraService:
    @classmethod
    @transaction.atomic
    def criar_pedido(
        cls, filial, usuario, fornecedor,
        data_entrega_prevista=None, observacao: str = '',
    ) -> PedidoCompra:
        numero = cls._gerar_numero_pedido(filial)
        return PedidoCompra.objects.create(
            filial=filial,
            numero_pedido=numero,
            fornecedor=fornecedor,
            usuario=usuario,
            status=PedidoCompra.Status.RASCUNHO,
            data_emissao=timezone.now(),
            data_entrega_prevista=data_entrega_prevista,
            observacao=observacao,
        )

    @staticmethod
    def _gerar_numero_pedido(filial) -> str:
        ultimo = PedidoCompra.objects.filter(filial=filial).order_by('-pk').first()
        if not ultimo or not ultimo.numero_pedido.startswith('PC-'):
            return 'PC-0000001'
        try:
            num = int(ultimo.numero_pedido.split('-')[1]) + 1
        except (ValueError, IndexError):
            num = 1
        return f'PC-{num:07d}'

    @classmethod
    @transaction.atomic
    def adicionar_item(
        cls, pedido: PedidoCompra, produto, quantidade: Decimal,
        valor_unitario: Decimal, valor_ipi: Decimal = Decimal('0'),
    ) -> ItemPedidoCompra:
        if pedido.status != PedidoCompra.Status.RASCUNHO:
            raise DadosInvalidosError('So e possivel adicionar itens em pedidos em rascunho.')
        if quantidade <= 0:
            raise DadosInvalidosError('Quantidade deve ser positiva.')

        numero_item = pedido.itens.count() + 1
        item = ItemPedidoCompra(
            pedido=pedido,
            produto=produto,
            numero_item=numero_item,
            quantidade=quantidade,
            valor_unitario=valor_unitario,
            valor_ipi=valor_ipi,
        )
        item.calcular_totais()
        item.save()

        pedido.recalcular_totais()
        pedido.save()
        return item

    @classmethod
    @transaction.atomic
    def remover_item(cls, pedido: PedidoCompra, item_id: int):
        if pedido.status != PedidoCompra.Status.RASCUNHO:
            raise DadosInvalidosError('So e possivel remover itens em pedidos em rascunho.')
        ItemPedidoCompra.objects.filter(pedido=pedido, pk=item_id).delete()
        pedido.recalcular_totais()
        pedido.save()

    @classmethod
    @transaction.atomic
    def aprovar_pedido(cls, pedido: PedidoCompra, usuario) -> PedidoCompra:
        if not pedido.pode_aprovar:
            raise DadosInvalidosError(
                f'Pedido nao pode ser aprovado no status "{pedido.get_status_display()}".'
            )
        if not pedido.itens.exists():
            raise DadosInvalidosError('Pedido sem itens nao pode ser aprovado.')

        pedido.status = PedidoCompra.Status.APROVADO
        pedido.aprovado_por = usuario
        pedido.data_aprovacao = timezone.now()
        pedido.save(update_fields=[
            'status', 'aprovado_por', 'data_aprovacao', 'updated_at',
        ])
        return pedido

    @classmethod
    @transaction.atomic
    def enviar_fornecedor(cls, pedido: PedidoCompra, usuario) -> PedidoCompra:
        if not pedido.pode_enviar:
            raise DadosInvalidosError(
                f'Pedido nao pode ser enviado no status "{pedido.get_status_display()}".'
            )
        pedido.status = PedidoCompra.Status.ENVIADO_FORNECEDOR
        pedido.save(update_fields=['status', 'updated_at'])
        return pedido

    @classmethod
    @transaction.atomic
    def cancelar_pedido(cls, pedido: PedidoCompra, usuario, motivo: str) -> PedidoCompra:
        if not pedido.pode_cancelar:
            raise DadosInvalidosError(
                f'Pedido nao pode ser cancelado no status "{pedido.get_status_display()}".'
            )
        if not motivo.strip():
            raise DadosInvalidosError('Informe o motivo do cancelamento.')
        pedido.status = PedidoCompra.Status.CANCELADO
        pedido.motivo_cancelamento = motivo
        pedido.save(update_fields=[
            'status', 'motivo_cancelamento', 'updated_at',
        ])
        return pedido

    @classmethod
    @transaction.atomic
    def criar_entrada_nf(
        cls, filial, usuario, fornecedor, numero_nf: str, serie_nf: str,
        data_emissao_nf, chave_acesso_nf: str = '',
        pedido_compra: PedidoCompra | None = None,
        observacao: str = '',
        origem_entrada: str = EntradaNF.OrigemEntrada.MANUAL,
        fornecedor_pendente: bool = False,
        dados_emitente_xml: dict | None = None,
    ) -> EntradaNF:
        chave_acesso_nf = ''.join(ch for ch in (chave_acesso_nf or '') if ch.isdigit())
        if chave_acesso_nf and len(chave_acesso_nf) != 44:
            raise DadosInvalidosError('Chave de acesso deve ter 44 digitos.')
        if chave_acesso_nf and EntradaNF.objects.for_filial(filial).filter(
            chave_acesso_nf=chave_acesso_nf,
        ).exists():
            raise DadosInvalidosError('Esta chave de acesso ja foi cadastrada nesta filial.')
        dados_emitente_xml = dados_emitente_xml or {}
        return EntradaNF.objects.create(
            filial=filial,
            pedido_compra=pedido_compra,
            fornecedor=fornecedor,
            numero_nf=numero_nf,
            serie_nf=serie_nf or '1',
            chave_acesso_nf=chave_acesso_nf,
            origem_entrada=origem_entrada,
            data_emissao_nf=data_emissao_nf,
            data_entrada=timezone.now(),
            status=EntradaNF.Status.RASCUNHO,
            usuario=usuario,
            fornecedor_pendente=fornecedor_pendente,
            emitente_cnpj_xml=dados_emitente_xml.get('documento', ''),
            emitente_razao_social_xml=dados_emitente_xml.get('razao_social', ''),
            emitente_nome_fantasia_xml=dados_emitente_xml.get('nome_fantasia', ''),
            emitente_ie_xml=dados_emitente_xml.get('ie', ''),
            emitente_endereco_xml=dados_emitente_xml.get('endereco', ''),
            emitente_municipio_xml=dados_emitente_xml.get('municipio', ''),
            emitente_uf_xml=dados_emitente_xml.get('uf', ''),
            emitente_cep_xml=dados_emitente_xml.get('cep', ''),
            emitente_telefone_xml=dados_emitente_xml.get('telefone', ''),
            observacao=observacao,
        )

    @classmethod
    @transaction.atomic
    def adicionar_item_entrada(
        cls, entrada: EntradaNF, produto,
        quantidade: Decimal, valor_unitario: Decimal,
        valor_ipi: Decimal = Decimal('0'), valor_icms: Decimal = Decimal('0'),
        numero_lote: str = '', data_fabricacao=None, data_validade=None,
        item_pedido_compra: ItemPedidoCompra | None = None,
        ean_xml: str = '',
        codigo_produto_fornecedor: str = '',
        descricao_xml: str = '',
        unidade_xml: str = '',
        unidade_estoque: str = '',
        fator_conversao: Decimal = Decimal('1'),
        quantidade_recebida: Decimal | None = None,
    ) -> ItemEntradaNF:
        if entrada.status not in (
            EntradaNF.Status.RASCUNHO,
            EntradaNF.Status.AGUARDANDO_VINCULOS,
            EntradaNF.Status.AGUARDANDO_CONFERENCIA,
            EntradaNF.Status.COM_DIFERENCAS,
            EntradaNF.Status.CONFERIDA,
        ):
            raise DadosInvalidosError('So e possivel adicionar itens em entradas abertas.')
        if quantidade <= 0:
            raise DadosInvalidosError('Quantidade deve ser positiva.')
        if produto and produto.controla_lote and not numero_lote:
            raise DadosInvalidosError(f'Produto "{produto}" requer numero de lote.')
        if produto and produto.controla_validade and not data_validade:
            raise DadosInvalidosError(f'Produto "{produto}" requer data de validade.')
        if produto and produto.controla_validade and data_validade < timezone.localdate():
            raise DadosInvalidosError(
                f'Produto "{produto}" nao pode entrar com validade vencida.'
            )

        fator_conversao = fator_conversao or Decimal('1')
        quantidade_estoque = quantidade * fator_conversao
        valor_bruto_xml = quantidade * valor_unitario
        valor_unitario_estoque = (
            valor_bruto_xml / quantidade_estoque
            if quantidade_estoque
            else valor_unitario
        )
        item = ItemEntradaNF(
            entrada=entrada,
            item_pedido_compra=item_pedido_compra,
            produto=produto,
            numero_item=entrada.itens.count() + 1,
            quantidade=quantidade_estoque,
            quantidade_xml=quantidade,
            quantidade_estoque=quantidade_estoque,
            quantidade_recebida=quantidade_recebida or quantidade_estoque,
            unidade_xml=unidade_xml,
            unidade_estoque=unidade_estoque or (
                produto.unidade_medida.sigla if produto and produto.unidade_medida_id else ''
            ),
            fator_conversao=fator_conversao,
            valor_unitario=valor_unitario_estoque,
            valor_ipi=valor_ipi,
            valor_icms=valor_icms,
            numero_lote=numero_lote,
            data_fabricacao=data_fabricacao,
            data_validade=data_validade,
            ean_xml=ean_xml,
            codigo_produto_fornecedor=codigo_produto_fornecedor,
            descricao_xml=descricao_xml,
        )
        item.calcular_totais()
        item.save()
        cls.atualizar_diferenca_item(item)

        cls._recalcular_totais_entrada(entrada)
        cls._atualizar_status_conferencia(entrada)
        return item

    @classmethod
    @transaction.atomic
    def remover_item_entrada(cls, item: ItemEntradaNF) -> dict:
        entrada = item.entrada
        if entrada.status not in (
            EntradaNF.Status.RASCUNHO,
            EntradaNF.Status.AGUARDANDO_VINCULOS,
            EntradaNF.Status.AGUARDANDO_CONFERENCIA,
            EntradaNF.Status.COM_DIFERENCAS,
            EntradaNF.Status.CONFERIDA,
        ):
            raise DadosInvalidosError('So e possivel remover itens de entradas abertas.')

        snapshot = {
            'id': item.pk,
            'numero_item': item.numero_item,
            'produto_id': item.produto_id,
            'produto': str(item.produto) if item.produto_id else '',
            'descricao_xml': item.descricao_xml,
            'ean_xml': item.ean_xml,
            'codigo_produto_fornecedor': item.codigo_produto_fornecedor,
            'quantidade_xml': str(item.quantidade_xml),
            'quantidade_recebida': str(item.quantidade_recebida),
            'valor_total': str(item.valor_total),
            'numero_lote': item.numero_lote,
            'data_validade': item.data_validade.isoformat() if item.data_validade else '',
        }
        item.delete()

        for novo_numero, item_restante in enumerate(
            entrada.itens.order_by('numero_item', 'pk'),
            start=1,
        ):
            if item_restante.numero_item != novo_numero:
                item_restante.numero_item = novo_numero
                item_restante.save(update_fields=['numero_item', 'updated_at'])

        cls._recalcular_totais_entrada(entrada)
        cls._atualizar_status_conferencia(entrada)
        return snapshot

    @staticmethod
    def _recalcular_totais_entrada(entrada: EntradaNF):
        from django.db.models import Sum
        agg = entrada.itens.aggregate(
            produtos=Sum('valor_bruto'),
            desconto=Sum('valor_desconto'),
            ipi=Sum('valor_ipi'),
            icms=Sum('valor_icms'),
            total=Sum('valor_total'),
        )
        entrada.valor_produtos = agg['produtos'] or 0
        entrada.valor_desconto = agg['desconto'] or 0
        entrada.valor_ipi = agg['ipi'] or 0
        entrada.valor_icms = agg['icms'] or 0
        entrada.valor_total = (
            (agg['total'] or 0)
            + entrada.valor_frete
            + entrada.valor_seguro
            + entrada.valor_outras_despesas
        )
        entrada.save()

    @classmethod
    @transaction.atomic
    def efetivar_entrada(
        cls,
        entrada: EntradaNF,
        usuario,
        confirmar_custo_critico: bool = False,
    ) -> EntradaNF:
        if not entrada.pode_efetivar:
            raise DadosInvalidosError(
                f'Entrada ja foi efetivada (status: {entrada.get_status_display()}).'
            )
        if not entrada.itens.exists():
            raise DadosInvalidosError('Entrada sem itens nao pode ser efetivada.')

        cls._validar_itens_para_efetivar(entrada)
        cls._validar_custo_para_efetivar(
            entrada,
            confirmar_custo_critico=confirmar_custo_critico,
        )
        EntradaCustoService.aplicar_configurada(entrada)

        entrada.status = EntradaNF.Status.PROCESSANDO
        entrada.save(update_fields=['status', 'updated_at'])

        for item in entrada.itens.select_related('produto', 'item_pedido_compra'):
            quantidade_movimento = item.quantidade_recebida
            if quantidade_movimento is None:
                quantidade_movimento = item.quantidade_estoque or item.quantidade
            custo_final = item.custo_unitario_total or item.valor_unitario

            if quantidade_movimento <= 0:
                item.quantidade = quantidade_movimento
                item.quantidade_estoque = quantidade_movimento
                item.quantidade_recebida = quantidade_movimento
                item.custo_unitario_total = Decimal('0')
                item.save(update_fields=[
                    'quantidade',
                    'quantidade_estoque',
                    'quantidade_recebida',
                    'custo_unitario_total',
                    'updated_at',
                ])
                continue

            mov = MovimentacaoService.registrar_entrada_compra(
                produto_id=item.produto_id,
                filial_id=entrada.filial_id,
                quantidade=quantidade_movimento,
                valor_unitario=custo_final,
                usuario_id=usuario.pk,
                fornecedor_id=entrada.fornecedor_id,
                numero_lote=item.numero_lote,
                data_fabricacao=item.data_fabricacao,
                data_validade=item.data_validade,
                numero_nota=entrada.numero_nf,
                documento_id=entrada.pk,
            )

            if mov.lote_id:
                item.lote_gerado_id = mov.lote_id
            item.quantidade = quantidade_movimento
            item.quantidade_estoque = quantidade_movimento
            item.quantidade_recebida = quantidade_movimento
            item.custo_unitario_total = custo_final
            item.save(update_fields=[
                'lote_gerado',
                'quantidade',
                'quantidade_estoque',
                'quantidade_recebida',
                'custo_unitario_total',
                'updated_at',
            ])

            if item.item_pedido_compra_id:
                ItemPedidoCompra.objects.filter(pk=item.item_pedido_compra_id).update(
                    quantidade_recebida=F('quantidade_recebida') + quantidade_movimento,
                )

        if entrada.pedido_compra_id:
            cls._atualizar_status_pedido(entrada.pedido_compra)
            cls._avaliar_fornecedor(entrada)

        entrada.status = EntradaNF.Status.EFETIVADA
        entrada.usuario_efetivacao = usuario
        entrada.data_efetivacao = timezone.now()
        entrada.save(update_fields=[
            'status', 'usuario_efetivacao', 'data_efetivacao', 'updated_at',
        ])
        return entrada

    @staticmethod
    def _validar_custo_para_efetivar(
        entrada: EntradaNF,
        confirmar_custo_critico: bool = False,
    ):
        composicao = EntradaCustoService.compor(
            entrada=entrada,
            metodo_rateio=entrada.custo_rateio_metodo,
            incluir_ipi=entrada.custo_incluir_ipi,
            incluir_icms_st=entrada.custo_incluir_icms_st,
            incluir_icms=entrada.custo_incluir_icms,
            custo_financeiro=entrada.custo_financeiro or Decimal('0'),
        )
        criticos = [
            linha for linha in composicao.get('alertas_custo', [])
            if linha.alerta_custo_nivel == 'critico'
        ]
        if criticos and not confirmar_custo_critico:
            raise DadosInvalidosError(
                'Custo critico exige confirmacao antes de finalizar a entrada. '
                'Revise a tela Custos e marque a confirmacao na finalizacao.'
            )

    @staticmethod
    def _validar_itens_para_efetivar(entrada: EntradaNF):
        bloqueios = []
        for item in entrada.itens.select_related('produto'):
            CompraService.atualizar_diferenca_item(item)
            if not item.produto_id:
                bloqueios.append(f'Item {item.numero_item}: produto sem vinculo.')
                continue
            quantidade_recebida = item.quantidade_recebida
            if quantidade_recebida is None:
                quantidade_recebida = item.quantidade_estoque or item.quantidade
            if quantidade_recebida <= 0:
                if not item.justificativa_diferenca.strip():
                    bloqueios.append(
                        f'Item {item.numero_item}: recebimento zerado exige justificativa.'
                    )
                continue
            if item.produto.controla_lote and not item.numero_lote:
                bloqueios.append(f'Item {item.numero_item}: lote obrigatorio nao informado.')
            if item.produto.controla_validade and not item.data_validade:
                bloqueios.append(f'Item {item.numero_item}: validade obrigatoria nao informada.')
            if (
                item.produto.controla_validade
                and item.data_validade
                and item.data_validade < timezone.localdate()
            ):
                bloqueios.append(f'Item {item.numero_item}: validade vencida nao pode movimentar estoque.')
            if item.diferenca_bloqueante:
                detalhe = item.diferenca_descricao or 'diferenca bloqueante'
                bloqueios.append(f'Item {item.numero_item}: {detalhe}.')
        if bloqueios:
            raise DadosInvalidosError('Nao e possivel finalizar: ' + ' '.join(bloqueios))

    @staticmethod
    def avaliar_diferenca_item(item) -> tuple[str, str, bool]:
        if not item.produto_id:
            return 'produto_sem_vinculo', 'Produto sem equivalencia interna.', True

        quantidade_recebida = item.quantidade_recebida
        if quantidade_recebida is None:
            quantidade_recebida = item.quantidade_estoque or item.quantidade
        if quantidade_recebida <= 0:
            return (
                'quantidade_recebida',
                (
                    'Item recusado na conferencia. '
                    f'Nota: {item.quantidade_estoque}, recebido: {quantidade_recebida}.'
                ),
                not bool(item.justificativa_diferenca.strip()),
            )

        hoje = timezone.localdate()
        if item.produto.controla_lote and not item.numero_lote:
            return 'lote_obrigatorio', 'Produto exige lote para movimentar estoque.', True
        if item.produto.controla_validade and not item.data_validade:
            return 'validade_obrigatoria', 'Produto exige validade para movimentar estoque.', True
        if (
            item.produto.controla_validade
            and item.data_validade
            and item.data_validade < hoje
        ):
            return (
                'validade_vencida',
                f'Validade vencida em {item.data_validade:%d/%m/%Y}.',
                True,
            )
        if (
            item.produto.controla_validade
            and item.data_validade
            and item.produto.dias_aviso_vencimento is not None
        ):
            dias_para_vencer = (item.data_validade - hoje).days
            if 0 <= dias_para_vencer <= item.produto.dias_aviso_vencimento:
                return (
                    'validade_proxima',
                    f'Validade proxima: vence em {dias_para_vencer} dia(s).',
                    False,
                )
        if item.quantidade_recebida != item.quantidade_estoque:
            return (
                'quantidade_recebida',
                (
                    f'Quantidade recebida diferente da nota. '
                    f'Nota: {item.quantidade_estoque}, recebido: {item.quantidade_recebida}.'
                ),
                not bool(item.justificativa_diferenca.strip()),
            )
        return '', '', False

    @staticmethod
    def atualizar_diferenca_item(item):
        tipo, descricao, bloqueante = CompraService.avaliar_diferenca_item(item)
        item.diferenca_tipo = tipo
        item.diferenca_descricao = descricao
        item.diferenca_bloqueante = bloqueante
        item.save(update_fields=[
            'quantidade_recebida',
            'numero_lote',
            'data_validade',
            'diferenca_tipo',
            'diferenca_descricao',
            'diferenca_bloqueante',
            'justificativa_diferenca',
            'updated_at',
        ])
        return item

    @staticmethod
    def _atualizar_status_conferencia(entrada: EntradaNF):
        itens = list(entrada.itens.all())
        if not itens:
            return
        if any(item.diferenca_bloqueante or not item.produto_id for item in itens):
            entrada.status = EntradaNF.Status.AGUARDANDO_VINCULOS
        elif any(item.diferenca_tipo for item in itens):
            entrada.status = EntradaNF.Status.COM_DIFERENCAS
        else:
            entrada.status = EntradaNF.Status.AGUARDANDO_CONFERENCIA
        entrada.save(update_fields=['status', 'updated_at'])

    @staticmethod
    def _atualizar_status_pedido(pedido: PedidoCompra):
        pedido.refresh_from_db()
        itens = list(pedido.itens.all())
        if all(item.recebido_completo for item in itens):
            pedido.status = PedidoCompra.Status.RECEBIDO
            pedido.data_entrega_realizada = timezone.localdate()
        else:
            pedido.status = PedidoCompra.Status.PARCIALMENTE_RECEBIDO
        pedido.save(update_fields=['status', 'data_entrega_realizada', 'updated_at'])

    @staticmethod
    def _avaliar_fornecedor(entrada: EntradaNF):
        pedido = entrada.pedido_compra
        if not pedido or not pedido.data_entrega_prevista:
            return

        data_real = entrada.data_entrada.date()
        dias_atraso = (data_real - pedido.data_entrega_prevista).days
        no_prazo = dias_atraso <= 0

        if dias_atraso <= 0:
            nota_pont = 5
        elif dias_atraso <= 2:
            nota_pont = 4
        elif dias_atraso <= 5:
            nota_pont = 3
        elif dias_atraso <= 10:
            nota_pont = 2
        else:
            nota_pont = 1

        avaliacao = AvaliacaoFornecedor.objects.create(
            filial=entrada.filial,
            fornecedor=entrada.fornecedor,
            pedido_compra=pedido,
            entrada_nf=entrada,
            data_prevista=pedido.data_entrega_prevista,
            data_real=data_real,
            dias_atraso=dias_atraso,
            entregue_no_prazo=no_prazo,
            nota_pontualidade=nota_pont,
            nota_qualidade=5,
            nota_geral=Decimal(str(nota_pont)),
        )

        fornecedor = entrada.fornecedor
        agg = AvaliacaoFornecedor.objects.filter(fornecedor=fornecedor).aggregate(
            media=Avg('nota_geral'),
        )
        total = AvaliacaoFornecedor.objects.filter(fornecedor=fornecedor).count()
        no_prazo_count = AvaliacaoFornecedor.objects.filter(
            fornecedor=fornecedor, entregue_no_prazo=True,
        ).count()

        fornecedor.nota_qualidade = agg['media'] or 0
        fornecedor.total_entregas = total
        fornecedor.entregas_no_prazo = no_prazo_count
        fornecedor.save(update_fields=[
            'nota_qualidade', 'total_entregas', 'entregas_no_prazo', 'updated_at',
        ])
        return avaliacao

    @classmethod
    @transaction.atomic
    def cancelar_entrada(cls, entrada: EntradaNF, usuario, motivo: str) -> EntradaNF:
        if not entrada.pode_cancelar:
            raise DadosInvalidosError(
                'So e possivel cancelar entradas abertas. Para efetivadas, use estorno.'
            )
        entrada.status = EntradaNF.Status.CANCELADA
        entrada.observacao = f'{entrada.observacao}\n[CANCELADA]: {motivo}'.strip()
        entrada.save(update_fields=['status', 'observacao', 'updated_at'])
        return entrada
