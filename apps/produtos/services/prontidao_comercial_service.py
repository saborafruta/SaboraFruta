"""Checagens comerciais de produto para estoque, entrada, vendas e PDV."""
from __future__ import annotations

from decimal import Decimal

from django.urls import reverse

from apps.produtos.models import Produto, ProdutoCodigoBarras
from apps.produtos.services.preco_service import PrecoService


STATUS_PRONTO = 'pronto_venda'
STATUS_COMERCIAL = 'pendente_comercial'
STATUS_CADASTRO = 'pendente_fiscal_cadastro'
STATUS_CUSTO = 'pendente_custo'


STATUS_LABELS = {
    STATUS_PRONTO: 'Pronto para venda',
    STATUS_COMERCIAL: 'Pendente comercial',
    STATUS_CADASTRO: 'Pendente fiscal/cadastro',
    STATUS_CUSTO: 'Pendente custo',
}


STATUS_CLASSES = {
    STATUS_PRONTO: 'bg-emerald-100 text-emerald-700',
    STATUS_COMERCIAL: 'bg-amber-100 text-amber-800',
    STATUS_CADASTRO: 'bg-blue-100 text-blue-700',
    STATUS_CUSTO: 'bg-red-100 text-red-700',
}


def _decimal(valor) -> Decimal:
    if valor in (None, ''):
        return Decimal('0')
    return Decimal(str(valor))


def custo_referencia(produto: Produto) -> Decimal:
    """Resolve o custo atual usado em margem/PDV sem depender de uma tela especifica."""
    estoque_custo = getattr(produto, 'estoque_custo_unitario', None)
    if _decimal(estoque_custo) > 0:
        return _decimal(estoque_custo)
    if _decimal(produto.preco_custo_medio) > 0:
        return _decimal(produto.preco_custo_medio)
    return _decimal(produto.preco_custo)


def produto_tem_codigo_barras(produto: Produto) -> bool:
    extras = produto.codigos_barras_extras if isinstance(produto.codigos_barras_extras, list) else []
    if (produto.codigo_barras or '').strip() or any(str(item).strip() for item in extras):
        return True
    return ProdutoCodigoBarras.objects.filter(produto=produto, ativo=True).exists()


def avaliar_produto_para_venda(produto: Produto, filial=None) -> dict:
    """Retorna pendencias que impedem ou fragilizam venda, promocao e PDV."""
    pendencias = []
    custo = custo_referencia(produto)
    preco_venda = _decimal(produto.preco_venda)
    preco_atual = _decimal(PrecoService.preco_vivo_produto(produto, filial=filial))

    def add(codigo, label, status, severidade='alerta'):
        pendencias.append({
            'codigo': codigo,
            'label': label,
            'status': status,
            'severidade': severidade,
        })

    if not getattr(produto, 'permite_venda', True):
        add(
            'produto_nao_vendavel',
            'Produto de uso interno (insumo, embalagem ou intermediário).',
            STATUS_COMERCIAL,
            'bloqueio',
        )

    if getattr(produto, 'rascunho_comercial', False):
        add(
            'rascunho_comercial',
            'Produto criado pelo XML esta em rascunho comercial.',
            STATUS_COMERCIAL,
            'bloqueio',
        )
    if preco_venda <= 0:
        add('sem_preco_venda', 'Sem preco de venda valido.', STATUS_COMERCIAL, 'bloqueio')
    if not produto_tem_codigo_barras(produto):
        add('sem_codigo_barras', 'Sem codigo de barras principal ou alternativo.', STATUS_CADASTRO)
    if not produto.categoria_id:
        add('sem_categoria', 'Sem categoria fiscal/cadastral.', STATUS_CADASTRO)
    if custo <= 0:
        add('sem_custo_valido', 'Sem custo valido para margem, CMV e promocao.', STATUS_CUSTO)
    if produto.margem_desejada and preco_atual > 0 and custo > 0 and produto.margem_atual < produto.margem_desejada:
        add(
            'margem_abaixo_minima',
            f'Margem atual abaixo da margem minima desejada ({produto.margem_desejada}%).',
            STATUS_COMERCIAL,
        )
    if not produto.ativo:
        add('produto_inativo', 'Produto inativo para operacao comercial.', STATUS_COMERCIAL, 'bloqueio')

    preco_detalhado = PrecoService.melhor_preco_produto_detalhado(produto, filial=filial)
    if (
        custo > 0
        and preco_detalhado.get('tipo') != 'normal'
        and _decimal(preco_detalhado.get('preco')) < custo
    ):
        add(
            'promocao_margem_negativa',
            'Promocao ativa com margem negativa contra o custo atual.',
            STATUS_COMERCIAL,
            'bloqueio',
        )

    if produto.controla_validade and not produto.controla_lote:
        add(
            'validade_sem_lote',
            'Produto controla validade sem controle de lote; venda FEFO fica inconsistente.',
            STATUS_CADASTRO,
            'bloqueio',
        )
    if produto.controla_validade and (
        produto.metodo_saida != Produto.MetodoSaida.FEFO or not produto.saida_fefo or produto.dias_aviso_vencimento <= 0
    ):
        add(
            'politica_validade_incompleta',
            'Produto com validade precisa de saida FEFO e dias de aviso configurados.',
            STATUS_CADASTRO,
        )
    if produto.controla_lote and produto.permite_venda_sem_estoque:
        add(
            'lote_venda_sem_estoque',
            'Produto com lote permite venda sem estoque; defina a regra antes do PDV.',
            STATUS_CADASTRO,
        )

    status = STATUS_PRONTO
    for candidato in (STATUS_CUSTO, STATUS_CADASTRO, STATUS_COMERCIAL):
        if any(item['status'] == candidato for item in pendencias):
            status = candidato
            break

    return {
        'produto': produto,
        'status': status,
        'label': STATUS_LABELS[status],
        'css_class': STATUS_CLASSES[status],
        'pendencias': pendencias,
        'pendencias_count': len(pendencias),
        'custo_referencia': custo,
        'preco_venda': preco_venda,
        'preco_atual': preco_atual,
        'edit_url': reverse('produtos:produto-update', args=[produto.pk]),
    }


def avaliar_produtos_para_venda(produtos, filial=None) -> dict[int, dict]:
    return {
        produto.pk: avaliar_produto_para_venda(produto, filial=filial)
        for produto in produtos
        if getattr(produto, 'pk', None)
    }


def anexar_prontidao_produtos(produtos, filial=None) -> list:
    avaliacoes = avaliar_produtos_para_venda(produtos, filial=filial)
    for produto in produtos:
        produto.prontidao_comercial = avaliacoes.get(produto.pk)
    return produtos


def avaliar_entrada_pos_efetivacao(entrada, itens) -> dict:
    produtos = []
    vistos = set()
    for item in itens:
        if not item.produto_id or item.quantidade_recebida <= 0 or getattr(item, 'item_recusado', False):
            continue
        if item.produto_id in vistos:
            continue
        vistos.add(item.produto_id)
        produtos.append(item.produto)

    avaliacoes = list(avaliar_produtos_para_venda(produtos, filial=entrada.filial).values())
    problematicos = [item for item in avaliacoes if item['status'] != STATUS_PRONTO]
    return {
        'avaliacoes': avaliacoes,
        'problematicos': problematicos,
        'prontos': [item for item in avaliacoes if item['status'] == STATUS_PRONTO],
        'total': len(avaliacoes),
        'problematicos_count': len(problematicos),
        'prontos_count': len(avaliacoes) - len(problematicos),
    }


def contrato_pdv_produto(produto: Produto, filial=None) -> dict:
    """Contrato tecnico para chamadas futuras de venda/PDV/promocoes."""
    avaliacao = avaliar_produto_para_venda(produto, filial=filial)
    return {
        'produto_id': produto.pk,
        'pode_vender': avaliacao['status'] == STATUS_PRONTO,
        'pode_promocionar_sem_alerta': (
            avaliacao['status'] == STATUS_PRONTO
            and avaliacao['custo_referencia'] > 0
            and avaliacao['preco_venda'] > 0
        ),
        'deve_consultar_saldo_filial': True,
        'deve_respeitar_lote_validade': bool(produto.controla_lote or produto.controla_validade),
        'deve_validar_margem_com_custo_atual': True,
        'pendencias': avaliacao['pendencias'],
    }
