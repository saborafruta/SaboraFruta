"""Sugestoes e cadastro rapido de produtos a partir da entrada de NF."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from apps.produtos.models import (
    Produto, ProdutoCodigoBarras, ProdutoFilial, ProdutoFornecedorEquivalencia,
    UnidadeMedida, UnidadeMedidaFilial,
)
from apps.produtos.services.replicacao_service import ReplicacaoProdutoService


STOPWORDS = {
    'com', 'das', 'dos', 'para', 'por', 'sem', 'und', 'unid', 'unit', 'pct',
    'pcte', 'cxs', 'cx', 'kg', 'ml', 'lt', 'litro', 'litros',
}

MARCADOR_VINCULO_REMOVIDO = '[vinculo-removido-manualmente]'


@dataclass
class ProdutoSugestao:
    produto: Produto
    score: int
    motivos: str


def _normalizar(valor: str) -> str:
    texto = unicodedata.normalize('NFKD', valor or '')
    texto = ''.join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r'[^a-z0-9]+', ' ', texto.lower()).strip()


def _tokens(valor: str) -> list[str]:
    tokens = []
    for token in _normalizar(valor).split():
        if len(token) < 3 or token in STOPWORDS:
            continue
        tokens.append(token)
    return tokens[:8]


def _ean_util(valor: str) -> str:
    texto = (valor or '').strip().upper()
    if texto in {'SEM GTIN', 'SEMGTIN', 'ISENTO'}:
        return ''
    return texto


def cfop_compra_por_cfop_xml(valor: str) -> str:
    cfop = re.sub(r'\D+', '', valor or '')[:4]
    mapa = {
        '5101': '1101',
        '6101': '2101',
        '5102': '1102',
        '6102': '2102',
        '5401': '1401',
        '6401': '2401',
        '5403': '1403',
        '6403': '2403',
        '5405': '1403',
        '6404': '2403',
        '6405': '2403',
        '5910': '1910',
        '6910': '2910',
    }
    if cfop in mapa:
        return mapa[cfop]
    if cfop.startswith('5'):
        return f'1{cfop[1:]}'
    if cfop.startswith('6'):
        return f'2{cfop[1:]}'
    if cfop.startswith('7'):
        return f'3{cfop[1:]}'
    return cfop


def cfops_padrao_para_item(item) -> dict[str, str]:
    return {
        'cfop_venda_interna': '5102',
        'cfop_venda_interestadual': '6102',
        'cfop_venda_exportacao': '7102',
        'cfop_compra': cfop_compra_por_cfop_xml(getattr(item, 'cfop_xml', '')) or '1102',
        'cfop_devolucao': '5202',
        'cfop_devolucao_compra': '1202',
    }


def _primeira_unidade(filial, sigla_preferida: str = '') -> UnidadeMedida:
    sigla = (sigla_preferida or 'UN').strip().upper()[:6] or 'UN'
    unidade = UnidadeMedida.objects.for_filial(filial).filter(
        empresa=filial.empresa,
        sigla__iexact=sigla,
        ativo=True,
    ).first()
    if not unidade:
        unidade = UnidadeMedida.objects.for_filial(filial).filter(
            empresa=filial.empresa,
            ativo=True,
        ).order_by('sigla').first()
    if unidade:
        return unidade

    unidade, _ = UnidadeMedida.objects.get_or_create(
        empresa=filial.empresa,
        sigla=sigla,
        defaults={'descricao': sigla, 'tipo': UnidadeMedida.Tipo.UNIDADE, 'ativo': True},
    )
    UnidadeMedidaFilial.objects.get_or_create(
        unidade=unidade,
        filial=filial,
        defaults={'ativo': True},
    )
    return unidade


def sugerir_produtos_para_item(item, filial, limite: int = 5) -> list[ProdutoSugestao]:
    termos = _tokens(item.descricao_xml)
    ean = _ean_util(item.ean_xml)
    ncm = item.ncm_xml or ''
    if not termos and not ean and not ncm:
        return []

    filtros = Q()
    for termo in termos[:5]:
        filtros |= Q(descricao__icontains=termo) | Q(descricao_curta__icontains=termo)
    if ncm:
        filtros |= Q(ncm=ncm)
    if ean:
        filtros |= Q(codigo_barras=ean) | Q(codigos_barras__ean=ean)

    candidatos = (
        Produto.objects.for_filial(filial)
        .filter(ativo=True)
        .filter(filtros)
        .select_related('unidade_medida')
        .distinct()[:80]
    )
    descricao_xml = _normalizar(item.descricao_xml)
    unidade_xml = (item.unidade_xml or '').strip().upper()
    sugestoes = []
    for produto in candidatos:
        descricao_produto = _normalizar(produto.descricao)
        score = 0
        motivos = []
        if ean and produto.codigo_barras == ean:
            score += 90
            motivos.append('EAN igual')
        if ncm and produto.ncm == ncm:
            score += 25
            motivos.append('NCM igual')
        tokens_batidos = [token for token in termos if token in descricao_produto]
        if tokens_batidos:
            score += min(45, len(tokens_batidos) * 12)
            motivos.append('nome parecido')
        if produto.codigo and produto.codigo in (item.codigo_produto_fornecedor or ''):
            score += 12
            motivos.append('codigo parecido')
        if unidade_xml and produto.unidade_medida.sigla.upper() == unidade_xml:
            score += 8
            motivos.append('unidade igual')
        if descricao_produto and descricao_produto in descricao_xml:
            score += 15
        if score >= 18:
            sugestoes.append(ProdutoSugestao(produto=produto, score=score, motivos=', '.join(motivos)))
    sugestoes.sort(key=lambda item_sugestao: item_sugestao.score, reverse=True)
    return sugestoes[:limite]


def sugestao_principal_para_item(item, filial) -> ProdutoSugestao | None:
    sugestoes = sugerir_produtos_para_item(item, filial, limite=1)
    if not sugestoes:
        return None
    return sugestoes[0]


def vincular_item_a_produto(
    entrada,
    item,
    produto: Produto,
    fator_conversao: Decimal | None = None,
    unidade_estoque: str = '',
    numero_lote: str | None = None,
    data_validade=None,
    origem_equivalencia: str = ProdutoFornecedorEquivalencia.Origem.MANUAL,
):
    fator = fator_conversao or item.fator_conversao or Decimal('1')
    unidade = unidade_estoque or produto.unidade_medida.sigla
    valor_bruto_original = item.valor_bruto or (
        (item.quantidade_xml or item.quantidade or Decimal('0')) * item.valor_unitario
    )
    item.produto = produto
    item.fator_conversao = fator
    item.unidade_estoque = unidade
    item.quantidade_estoque = item.quantidade_xml * fator
    item.quantidade_recebida = item.quantidade_estoque
    item.quantidade = item.quantidade_estoque
    if item.quantidade_estoque:
        item.valor_unitario = valor_bruto_original / item.quantidade_estoque
    if numero_lote is not None:
        item.numero_lote = numero_lote
    if data_validade:
        item.data_validade = data_validade
    if item.observacao and MARCADOR_VINCULO_REMOVIDO in item.observacao:
        item.observacao = item.observacao.replace(MARCADOR_VINCULO_REMOVIDO, '').strip()
    item.diferenca_tipo = ''
    item.diferenca_descricao = ''
    item.diferenca_bloqueante = False
    item.calcular_totais()
    item.save()
    from apps.compras.services.compra_service import CompraService
    CompraService.atualizar_diferenca_item(item)

    ean = _ean_util(item.ean_xml)
    if ean:
        codigo_barras = ProdutoCodigoBarras.objects.filter(
            produto=produto,
            ean=ean,
        ).first()
        if codigo_barras:
            codigo_barras.tipo = ProdutoCodigoBarras.Tipo.FORNECEDOR
            codigo_barras.quantidade_conversao = fator
            codigo_barras.ativo = True
            codigo_barras.save(update_fields=['tipo', 'quantidade_conversao', 'ativo', 'updated_at'])
        else:
            ProdutoCodigoBarras.objects.create(
                produto=produto,
                ean=ean,
                tipo=ProdutoCodigoBarras.Tipo.FORNECEDOR,
                quantidade_conversao=fator,
                ativo=True,
                observacao='Criado por conferencia de entrada.',
            )
    ProdutoFornecedorEquivalencia.objects.update_or_create(
        fornecedor=None if entrada.fornecedor_pendente else entrada.fornecedor,
        fornecedor_cnpj_xml=entrada.emitente_cnpj_xml,
        codigo_fornecedor=item.codigo_produto_fornecedor,
        ean_utilizado=ean,
        defaults={
            'fornecedor_razao_social_xml': entrada.emitente_razao_social_xml,
            'produto': produto,
            'descricao_fornecedor': item.descricao_xml,
            'unidade_compra': item.unidade_xml,
            'unidade_estoque': unidade,
            'fator_conversao': fator,
            'ultimo_custo': item.valor_unitario,
            'data_ultima_compra': entrada.data_emissao_nf,
            'origem': origem_equivalencia,
            'ativo': True,
        },
    )
    return item


def desvincular_item_de_produto(item):
    from apps.compras.services.compra_service import CompraService

    observacao = str(item.observacao or '').replace(MARCADOR_VINCULO_REMOVIDO, '').strip()
    if observacao:
        limite_observacao = 255 - len(MARCADOR_VINCULO_REMOVIDO) - 1
        observacao = f'{observacao[:limite_observacao].rstrip()} {MARCADOR_VINCULO_REMOVIDO}'
    else:
        observacao = MARCADOR_VINCULO_REMOVIDO
    item.produto = None
    item.observacao = observacao
    item.diferenca_tipo = ''
    item.diferenca_descricao = ''
    item.diferenca_bloqueante = False
    item.save(update_fields=[
        'produto',
        'observacao',
        'diferenca_tipo',
        'diferenca_descricao',
        'diferenca_bloqueante',
        'updated_at',
    ])
    CompraService.atualizar_diferenca_item(item)
    return item


def _revinculo_manual_liberado_por_ean(item, resolvido) -> bool:
    if MARCADOR_VINCULO_REMOVIDO not in (item.observacao or ''):
        return True
    if not item.ean_xml or not resolvido.produto:
        return False
    if resolvido.origem not in {'codigo_barras', 'ean_alternativo_json', 'produto_codigo_barras'}:
        return False
    return bool(resolvido.produto.updated_at and item.updated_at and resolvido.produto.updated_at > item.updated_at)


@transaction.atomic
def reprocessar_vinculos_automaticos(entrada) -> dict[str, int]:
    """Tenta vincular itens pendentes por identificadores seguros ja cadastrados."""
    from apps.compras.services.compra_service import CompraService
    from apps.compras.services.entrada_xml_service import resolver_produto

    vinculados = 0
    pendentes = 0
    itens = (
        entrada.itens
        .select_for_update()
        .filter(produto__isnull=True)
        .filter(produtos_gerados__isnull=True)
        .order_by('numero_item')
    )
    for item in itens:
        resolvido = resolver_produto(
            filial=entrada.filial,
            ean=item.ean_xml,
            codigo_fornecedor=item.codigo_produto_fornecedor,
            fornecedor=None if entrada.fornecedor_pendente else entrada.fornecedor,
            fornecedor_cnpj_xml=entrada.emitente_cnpj_xml,
        )
        if not resolvido.produto or not _revinculo_manual_liberado_por_ean(item, resolvido):
            pendentes += 1
            CompraService.atualizar_diferenca_item(item)
            continue
        vincular_item_a_produto(
            entrada=entrada,
            item=item,
            produto=resolvido.produto,
            fator_conversao=resolvido.fator_conversao,
            unidade_estoque=resolvido.unidade_estoque or resolvido.produto.unidade_medida.sigla,
            numero_lote=item.numero_lote,
            data_validade=item.data_validade,
            origem_equivalencia=ProdutoFornecedorEquivalencia.Origem.XML,
        )
        vinculados += 1

    CompraService._atualizar_status_conferencia(entrada)
    return {'vinculados': vinculados, 'pendentes': pendentes}


def _equivalencias_do_item(entrada, item):
    """As equivalencias que ligariam ESTE produto a ESTE fornecedor."""
    por_fornecedor = Q()
    if entrada.emitente_cnpj_xml:
        por_fornecedor |= Q(fornecedor_cnpj_xml=entrada.emitente_cnpj_xml)
    if entrada.fornecedor_id and not entrada.fornecedor_pendente:
        por_fornecedor |= Q(fornecedor_id=entrada.fornecedor_id)
    if not por_fornecedor:
        return ProdutoFornecedorEquivalencia.objects.none()
    return ProdutoFornecedorEquivalencia.objects.filter(
        por_fornecedor, produto_id=item.produto_id,
    )


@transaction.atomic
def sincronizar_vinculos_da_conferencia(entrada) -> dict[str, int]:
    """
    Poe a nota em dia com os vinculos ANTES de mostra-la ao conferente.

    Duas correcoes, nesta ordem, porque a segunda depende da primeira.

    1. SOLTA O QUE PERDEU A JUSTIFICATIVA. Desativar a equivalencia no cadastro
       do produto so' cuidava das notas abertas naquele instante. Nota
       importada depois continuava exibindo o item preso a um produto cuja
       ligacao ja' foi desfeita -- e o conferente nao tinha como saber que
       aquele vinculo nao valia mais.

    2. AMARRA O QUE PASSOU A TER COMO. `reprocessar_vinculos_automaticos` ja'
       fazia isso, mas so' por botao: quem nao clicasse via a nota com itens
       pendentes que o sistema ja' sabia resolver.

    SO' MEXE NO QUE VEIO DE EQUIVALENCIA. Item sem equivalencia nenhuma foi
    amarrado por outro caminho -- EAN do proprio produto, escolha manual antes
    de a regra existir -- e solta-lo destruiria trabalho alheio. A condicao e'
    estreita de proposito: EXISTE equivalencia para este par e TODAS estao
    inativas.

    O MARCADOR SOBREVIVE. Item que o conferente desvinculou a mao ja' vem com o
    carimbo, e o passo 2 respeita o carimbo -- passar por aqui nao desfaz a
    decisao dele.
    """
    soltos = 0
    itens = (
        entrada.itens
        .select_for_update()
        .filter(produto__isnull=False)
        .select_related('produto')
        .order_by('numero_item')
    )
    for item in itens:
        equivalencias = _equivalencias_do_item(entrada, item)
        if not equivalencias.exists():
            continue
        if equivalencias.filter(ativo=True).exists():
            continue
        desvincular_item_de_produto(item)
        soltos += 1

    resultado = reprocessar_vinculos_automaticos(entrada)
    resultado['soltos'] = soltos
    return resultado


def _produto_existente_para_item(entrada, item, ean: str) -> Produto | None:
    produtos_filial = (
        Produto.objects.for_filial(entrada.filial)
        .filter(ativo=True)
        .select_related('unidade_medida')
    )
    if ean:
        produto = (
            produtos_filial
            .filter(
                Q(codigo_barras=ean)
                | Q(codigos_barras__ean=ean, codigos_barras__ativo=True)
            )
            .distinct()
            .first()
        )
        if produto:
            return produto

    codigo_fornecedor = (item.codigo_produto_fornecedor or '').strip()
    filtro_vinculo = Q()
    if codigo_fornecedor:
        filtro_vinculo |= Q(codigo_fornecedor=codigo_fornecedor)
    if ean:
        filtro_vinculo |= Q(ean_utilizado=ean)
    if not filtro_vinculo:
        return None

    filtro_fornecedor = Q(fornecedor_cnpj_xml=entrada.emitente_cnpj_xml)
    if not entrada.fornecedor_pendente:
        filtro_fornecedor |= Q(fornecedor=entrada.fornecedor)

    equivalencia = (
        ProdutoFornecedorEquivalencia.objects
        .filter(filtro_fornecedor, filtro_vinculo, ativo=True)
        .filter(
            produto__ativo=True,
            produto__filiais_vinculo__filial=entrada.filial,
            produto__filiais_vinculo__ativo=True,
        )
        .select_related('produto', 'produto__unidade_medida')
        .first()
    )
    return equivalencia.produto if equivalencia else None


@transaction.atomic
def criar_produto_e_vincular_item(entrada, item) -> Produto:
    unidade = _primeira_unidade(entrada.filial, item.unidade_estoque or item.unidade_xml)
    ean = _ean_util(item.ean_xml)
    produto_existente = _produto_existente_para_item(entrada, item, ean)
    if produto_existente:
        vincular_item_a_produto(
            entrada,
            item,
            produto_existente,
            fator_conversao=item.fator_conversao or Decimal('1'),
            unidade_estoque=produto_existente.unidade_medida.sigla,
            origem_equivalencia=ProdutoFornecedorEquivalencia.Origem.XML,
        )
        return produto_existente

    codigo_barras = ean if ean.isdigit() and len(ean) <= 14 else ''
    descricao = (item.descricao_xml or f'Produto NF {entrada.numero_nf} item {item.numero_item}').strip()
    preco_custo = item.valor_unitario or Decimal('0')
    controla_lote = bool(item.numero_lote or item.data_fabricacao or item.data_validade)
    controla_validade = bool(item.data_validade)
    produto = Produto(
        filial=entrada.filial,
        fornecedor=None if entrada.fornecedor_pendente else entrada.fornecedor,
        unidade_medida=unidade,
        unidade_medida_compra=unidade,
        fator_conversao_compra=item.fator_conversao or Decimal('1'),
        codigo=(item.codigo_produto_fornecedor or '')[:30],
        codigo_barras=codigo_barras,
        descricao=descricao[:150],
        descricao_curta=descricao[:120],
        descricao_pdv=descricao[:80],
        ncm=(item.ncm_xml or '00000000')[:8],
        **cfops_padrao_para_item(item),
        preco_custo=preco_custo,
        preco_venda=preco_custo,
        preco_minimo=preco_custo,
        controla_lote=controla_lote,
        controla_validade=controla_validade,
        permite_venda_sem_estoque=False,
        rascunho_comercial=True,
        observacao=(
            'Criado a partir da entrada XML. Revisar cadastro fiscal/comercial '
            'antes da venda.'
        ),
        ativo=True,
    )
    produto.calcular_margem()
    produto.save()
    ProdutoFilial.objects.update_or_create(
        produto=produto,
        filial=entrada.filial,
        defaults={'ativo': True},
    )
    try:
        ReplicacaoProdutoService.sincronizar_produto(produto)
    except Exception:
        pass
    vincular_item_a_produto(
        entrada,
        item,
        produto,
        fator_conversao=item.fator_conversao or Decimal('1'),
        unidade_estoque=unidade.sigla,
        origem_equivalencia=ProdutoFornecedorEquivalencia.Origem.XML,
    )
    return produto
