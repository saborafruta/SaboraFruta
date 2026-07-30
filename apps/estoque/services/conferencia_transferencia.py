from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.core.services.auditoria import registrar_auditoria
from apps.core.services.notificacao_service import (
    notificar_transferencia_conferida,
    notificar_transferencia_recebida,
)
from apps.estoque.models import (
    ConferenciaTransferencia,
    ItemConferenciaTransferencia,
    LoteProduto,
    MovimentacaoEstoque,
)
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.produtos.models import Produto


def _itens_auditoria(conferencia):
    return [
        {
            'produto_enviado_id': item.produto_enviado_id,
            'produto_enviado': item.produto_enviado.descricao,
            'quantidade_enviada': str(item.quantidade_enviada),
            'quantidade_recebida': str(item.quantidade_recebida),
            'ocorrencia': item.ocorrencia,
            'produto_recebido_id': item.produto_recebido_id,
            'produto_recebido': (
                item.produto_recebido.descricao if item.produto_recebido_id else ''
            ),
            'quantidade_trocada': str(item.quantidade_produto_recebido),
            'quantidade_devolvida': str(item.quantidade_devolvida),
            'observacao': item.observacao,
        }
        for item in conferencia.itens.select_related(
            'produto_enviado', 'produto_recebido',
        )
    ]


@transaction.atomic
def criar_conferencia_transferencia(
    *,
    documento_numero,
    filial_origem,
    filial_destino,
    usuario,
    observacao='',
):
    conferencia, criada = ConferenciaTransferencia.objects.get_or_create(
        documento_numero=documento_numero,
        defaults={
            'filial_origem': filial_origem,
            'filial_destino': filial_destino,
            'criada_por': usuario,
            'observacao_origem': observacao,
        },
    )
    if criada:
        movimentos = (
            MovimentacaoEstoque.objects
            .filter(
                filial=filial_origem,
                filial_destino=filial_destino,
                documento_numero=documento_numero,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
            )
            .select_related('produto', 'lote')
            .order_by('pk')
        )
        for movimento in movimentos:
            ItemConferenciaTransferencia.objects.create(
                conferencia=conferencia,
                movimento_saida=movimento,
                produto_enviado=movimento.produto,
                lote_enviado=movimento.lote,
                quantidade_enviada=movimento.quantidade,
                quantidade_recebida=movimento.quantidade,
            )
        registrar_auditoria(
            usuario=usuario,
            filial=filial_origem,
            modulo='estoque',
            acao='transferir',
            objeto=conferencia,
            descricao=f'Transferencia {conferencia.documento_numero} enviada',
            metadados={
                'evento': 'transferencia_enviada',
                'filial_origem': filial_origem.nome_fantasia or filial_origem.razao_social,
                'filial_destino': filial_destino.nome_fantasia or filial_destino.razao_social,
                'observacao': observacao,
                'itens': _itens_auditoria(conferencia),
            },
        )
    notificar_transferencia_recebida(conferencia)
    return conferencia


def garantir_conferencias_recebidas(filial_destino):
    entradas = (
        MovimentacaoEstoque.objects
        .filter(
            filial=filial_destino,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_ENTRADA,
            transferencia_cancelada=False,
        )
        .exclude(documento_numero='')
        .exclude(documento_numero__startswith='EST-')
        .exclude(documento_numero__startswith='RAT-')
        .select_related('usuario')
        .order_by('-data_movimentacao')
    )
    documentos = list(dict.fromkeys(
        entradas.values_list('documento_numero', flat=True),
    ))
    conferencias = {
        item.documento_numero: item
        for item in ConferenciaTransferencia.objects.filter(
            filial_destino=filial_destino,
            documento_numero__in=documentos,
        )
    }
    criadas = []
    for documento_numero in documentos:
        conferencia = conferencias.get(documento_numero)
        if conferencia:
            if conferencia.status == ConferenciaTransferencia.Status.AGUARDANDO:
                notificar_transferencia_recebida(conferencia)
            continue
        saida = (
            MovimentacaoEstoque.objects
            .filter(
                documento_numero=documento_numero,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
                filial_destino=filial_destino,
                transferencia_cancelada=False,
            )
            .select_related('filial', 'usuario')
            .first()
        )
        if not saida:
            continue
        conferencia = criar_conferencia_transferencia(
            documento_numero=documento_numero,
            filial_origem=saida.filial,
            filial_destino=filial_destino,
            usuario=saida.usuario,
            observacao=saida.observacao,
        )
        conferencias[documento_numero] = conferencia
        criadas.append(conferencia)
    return criadas


def _decimal_positivo(valor, rotulo, permite_zero=True):
    try:
        texto = str(valor or '0').strip()
        if ',' in texto and '.' in texto:
            texto = texto.replace('.', '').replace(',', '.')
        else:
            texto = texto.replace(',', '.')
        numero = Decimal(texto)
    except (InvalidOperation, TypeError, ValueError):
        raise DadosInvalidosError(f'{rotulo} invalida.')
    if numero < 0 or (not permite_zero and numero == 0):
        raise DadosInvalidosError(f'{rotulo} deve ser maior que zero.')
    return numero


def _lote_destino(item):
    if not item.lote_enviado_id:
        return None
    return LoteProduto.objects.filter(
        produto_id=item.produto_enviado_id,
        filial_id=item.conferencia.filial_destino_id,
        numero_lote=item.lote_enviado.numero_lote,
    ).first()


@transaction.atomic
def concluir_conferencia(*, conferencia_id, filial_destino, usuario, itens, observacao=''):
    conferencia = (
        ConferenciaTransferencia.objects
        .select_for_update()
        .select_related('filial_origem', 'filial_destino')
        .get(pk=conferencia_id, filial_destino=filial_destino)
    )
    if conferencia.status in {
        ConferenciaTransferencia.Status.CONFERIDA,
        ConferenciaTransferencia.Status.COM_DIVERGENCIA,
        ConferenciaTransferencia.Status.CANCELADA,
    }:
        raise DadosInvalidosError('Esta transferencia ja foi finalizada.')

    itens_por_id = {str(item.pk): item for item in conferencia.itens.select_related(
        'produto_enviado', 'lote_enviado',
    )}
    if set(itens_por_id) != set(itens):
        raise DadosInvalidosError('Confira todos os itens antes de concluir.')

    tem_divergencia = False
    documento_ajuste = f'CONF-{conferencia.pk}'[:20]
    for item_id, dados in itens.items():
        item = itens_por_id[item_id]
        ocorrencia = dados.get('ocorrencia') or ItemConferenciaTransferencia.Ocorrencia.OK
        observacao_item = (dados.get('observacao') or '').strip()
        if ocorrencia not in ItemConferenciaTransferencia.Ocorrencia.values:
            raise DadosInvalidosError('Tipo de ocorrencia invalido.')
        if (
            ocorrencia != ItemConferenciaTransferencia.Ocorrencia.OK
            and not observacao_item
        ):
            raise DadosInvalidosError(
                f'Explique a divergencia de {item.produto_enviado.descricao}.'
            )

        recebida = _decimal_positivo(
            dados.get('quantidade_recebida'),
            f'Quantidade recebida de {item.produto_enviado.descricao}',
        )
        if recebida > item.quantidade_enviada:
            raise DadosInvalidosError(
                f'A quantidade recebida de {item.produto_enviado.descricao} '
                'nao pode superar a quantidade enviada.'
            )

        produto_recebido = None
        quantidade_trocada = Decimal('0')
        quantidade_devolvida = Decimal('0')
        item_devolvido = ocorrencia == ItemConferenciaTransferencia.Ocorrencia.DEVOLVIDO
        if ocorrencia == ItemConferenciaTransferencia.Ocorrencia.OK:
            recebida = item.quantidade_enviada
        elif ocorrencia == ItemConferenciaTransferencia.Ocorrencia.TROCADO:
            produto_recebido = Produto.objects.for_filial(filial_destino).filter(
                pk=dados.get('produto_recebido_id'),
                ativo=True,
            ).first()
            quantidade_trocada = _decimal_positivo(
                dados.get('quantidade_produto_recebido'),
                'Quantidade do produto recebido no lugar',
                permite_zero=False,
            )
            if not produto_recebido:
                raise DadosInvalidosError('Selecione o produto recebido no lugar.')
            if quantidade_trocada > item.quantidade_enviada:
                raise DadosInvalidosError(
                    f'A quantidade trocada de {item.produto_enviado.descricao} '
                    'nao pode superar a quantidade enviada.'
                )
            recebida = item.quantidade_enviada - quantidade_trocada
        elif item_devolvido:
            quantidade_devolvida = _decimal_positivo(
                dados.get('quantidade_devolvida'),
                f'Quantidade devolvida de {item.produto_enviado.descricao}',
                permite_zero=False,
            )
            if quantidade_devolvida > item.quantidade_enviada:
                raise DadosInvalidosError(
                    f'A quantidade devolvida de {item.produto_enviado.descricao} '
                    'nao pode superar a quantidade enviada.'
                )
            recebida = item.quantidade_enviada - quantidade_devolvida
            lote_destino = _lote_destino(item)
            MovimentacaoService.transferir_entre_filiais(
                produto_id=item.produto_enviado_id,
                filial_origem_id=filial_destino.pk,
                filial_destino_id=conferencia.filial_origem_id,
                quantidade=quantidade_devolvida,
                usuario_id=usuario.pk,
                lote_id=lote_destino.pk if lote_destino else None,
                observacao=(
                    f'Devolucao na conferencia da transferencia '
                    f'{conferencia.documento_numero}: {observacao_item}'
                ),
                permitir_sem_lote=True,
                vincular_destino=True,
                documento_numero=f'DEV-{conferencia.pk}-{item.pk}'[:20],
            )
            tem_divergencia = True

        diferenca = item.quantidade_enviada - recebida
        if diferenca > 0 and not item_devolvido:
            lote_destino = _lote_destino(item)
            MovimentacaoService.registrar_movimentacao(
                produto_id=item.produto_enviado_id,
                filial_id=filial_destino.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.AJUSTE_MENOS,
                quantidade=diferenca,
                usuario_id=usuario.pk,
                lote_id=lote_destino.pk if lote_destino else None,
                valor_unitario=item.movimento_saida.valor_unitario,
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.AJUSTE_MANUAL,
                documento_numero=documento_ajuste,
                observacao=(
                    f'Divergencia na conferencia da transferencia '
                    f'{conferencia.documento_numero}: item faltante/trocado.'
                ),
                forcar_estoque_negativo=True,
                permitir_sem_lote=True,
            )
            tem_divergencia = True

        if produto_recebido and quantidade_trocada > 0:
            MovimentacaoService.registrar_movimentacao(
                produto_id=produto_recebido.pk,
                filial_id=filial_destino.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.AJUSTE_MAIS,
                quantidade=quantidade_trocada,
                usuario_id=usuario.pk,
                valor_unitario=produto_recebido.preco_custo_medio or produto_recebido.preco_custo,
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.AJUSTE_MANUAL,
                documento_numero=documento_ajuste,
                observacao=(
                    f'Produto recebido no lugar durante a conferencia da '
                    f'transferencia {conferencia.documento_numero}.'
                ),
                permitir_sem_lote=True,
            )
            tem_divergencia = True

        item.quantidade_recebida = recebida
        item.ocorrencia = ocorrencia
        item.produto_recebido = produto_recebido
        item.quantidade_produto_recebido = quantidade_trocada
        item.quantidade_devolvida = quantidade_devolvida
        item.observacao = observacao_item
        item.save()
        tem_divergencia = tem_divergencia or ocorrencia != ItemConferenciaTransferencia.Ocorrencia.OK

    conferencia.status = (
        ConferenciaTransferencia.Status.COM_DIVERGENCIA
        if tem_divergencia
        else ConferenciaTransferencia.Status.CONFERIDA
    )
    conferencia.observacao_conferencia = (observacao or '').strip()
    conferencia.conferida_por = usuario
    conferencia.conferida_em = timezone.now()
    conferencia.save()
    registrar_auditoria(
        usuario=usuario,
        filial=filial_destino,
        modulo='estoque',
        acao='efetivar',
        objeto=conferencia,
        descricao=(
            f'Transferencia {conferencia.documento_numero} '
            f'{conferencia.get_status_display().lower()}'
        ),
        justificativa=conferencia.observacao_conferencia,
        metadados={
            'evento': 'conferencia_concluida',
            'filial_origem': (
                conferencia.filial_origem.nome_fantasia
                or conferencia.filial_origem.razao_social
            ),
            'filial_destino': (
                conferencia.filial_destino.nome_fantasia
                or conferencia.filial_destino.razao_social
            ),
            'status': conferencia.status,
            'itens': _itens_auditoria(conferencia),
        },
    )
    notificar_transferencia_conferida(conferencia)
    return conferencia


@transaction.atomic
def cancelar_na_conferencia(*, conferencia_id, filial_destino, usuario):
    from apps.estoque.services.transferencia_cancelamento import cancelar_transferencia

    conferencia = (
        ConferenciaTransferencia.objects
        .select_for_update()
        .select_related('filial_origem')
        .get(pk=conferencia_id, filial_destino=filial_destino)
    )
    if conferencia.status != ConferenciaTransferencia.Status.AGUARDANDO:
        raise DadosInvalidosError(
            'Somente uma transferencia aguardando conferencia pode ser cancelada aqui.'
        )
    cancelar_transferencia(
        conferencia.documento_numero,
        conferencia.filial_origem,
        usuario,
    )
    conferencia.status = ConferenciaTransferencia.Status.CANCELADA
    conferencia.conferida_por = usuario
    conferencia.conferida_em = timezone.now()
    conferencia.save()
    registrar_auditoria(
        usuario=usuario,
        filial=filial_destino,
        modulo='estoque',
        acao='cancelar',
        objeto=conferencia,
        descricao=f'Transferencia {conferencia.documento_numero} cancelada no recebimento',
        metadados={
            'evento': 'transferencia_cancelada',
            'filial_origem': (
                conferencia.filial_origem.nome_fantasia
                or conferencia.filial_origem.razao_social
            ),
            'filial_destino': (
                conferencia.filial_destino.nome_fantasia
                or conferencia.filial_destino.razao_social
            ),
            'itens': _itens_auditoria(conferencia),
        },
    )
    notificar_transferencia_conferida(conferencia)
    return conferencia
