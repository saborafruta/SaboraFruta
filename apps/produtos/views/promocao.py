import logging

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from decimal import Decimal, ROUND_HALF_UP

from apps.core.models import Filial
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.produtos.forms import (
    BrindeProdutoForm,
    BrindeProdutoItemFormSet,
    KitCategoriaForm,
    KitCategoriaRegraFormSet,
    KitProdutoForm,
    KitProdutoItemFormSet,
    PrecoPromocionalItemFormSet,
    PromocaoQuantidadeFaixaFormSet,
    PromocaoQuantidadeForm,
)
from apps.produtos.models import (
    BrindeProduto,
    BrindeProdutoItem,
    CondicaoQuantidade,
    DIAS_SEMANA_TODOS,
    KitCategoria,
    KitCategoriaRegra,
    KitProduto,
    KitProdutoItem,
    Produto,
    ProdutoFilial,
    PromocaoQuantidade,
    PromocaoQuantidadeFaixa,
)
from apps.produtos.services.replicacao_service import ReplicacaoProdutoService
from apps.produtos.services.preco_service import PrecoService
from apps.produtos.views.promocao_audit import promocao_log_context


logger = logging.getLogger(__name__)

DIAS_SEMANA_LABELS = {
    '0': 'Seg',
    '1': 'Ter',
    '2': 'Qua',
    '3': 'Qui',
    '4': 'Sex',
    '5': 'Sab',
    '6': 'Dom',
}
MIN_DIAS_PROMO_AUTOMATICA = 5


def _filial_nome(filial):
    return filial.nome_fantasia or filial.razao_social or str(filial.pk)


def _filiais_destino(filial, campo_politica='replicar_produtos_basicos'):
    politica = ReplicacaoProdutoService._politica(filial)
    if not politica or not getattr(politica, campo_politica, False):
        return Filial.objects.none()
    return ReplicacaoProdutoService._filiais_destino(filial, campo_politica)


def _filiais_replicacao_context(filial, campo_politica='replicar_produtos_basicos'):
    if not filial or not getattr(filial, 'empresa_id', None):
        return []
    try:
        filiais = Filial.objects.filter(
            empresa=filial.empresa,
            ativo=True,
        ).exclude(pk=filial.pk).order_by('nome_fantasia', 'razao_social', 'pk')
        politica_origem = ReplicacaoProdutoService._politica(filial)
        origem_ok = bool(
            politica_origem
            and getattr(politica_origem, campo_politica, False)
            and getattr(filial, 'participa_replicacao', False)
        )
        itens = []
        for destino in filiais:
            motivo = ''
            politica_destino = ReplicacaoProdutoService._politica(destino)
            if not origem_ok:
                motivo = 'A filial atual nao permite essa replicacao.'
            elif not getattr(destino, 'participa_replicacao', False):
                motivo = 'Esta filial nao participa da replicacao.'
            elif not politica_destino or not getattr(politica_destino, campo_politica, False):
                motivo = 'Politica de replicacao desativada para esta filial.'
            itens.append({'id': destino.pk, 'nome': _filial_nome(destino), 'habilitada': not motivo, 'motivo': motivo})
        return itens
    except Exception:
        logger.exception('Falha ao montar opcoes de replicacao de promocoes.')
        return []


def _filiais_destino_request(request, campo_politica='replicar_produtos_basicos'):
    destinos = _filiais_destino(request.filial_ativa, campo_politica)
    if request.POST.get('replicacao_seletiva') == '1':
        ids = [_as_int(valor) for valor in request.POST.getlist('replicar_filiais_destino')]
        ids = [valor for valor in ids if valor]
        if not ids:
            return destinos.none()
        destinos = destinos.filter(pk__in=ids)
    return destinos


def _relatorio_replicacao():
    return {'replicadas': [], 'ignoradas': []}


def _registrar_replicada(relatorio, filial):
    nome = _filial_nome(filial)
    if nome not in relatorio['replicadas']:
        relatorio['replicadas'].append(nome)


def _registrar_ignorada(relatorio, filial, motivo):
    relatorio['ignoradas'].append(f'{_filial_nome(filial)}: {motivo}')


def _avisar_replicacao(request, relatorio):
    if not relatorio:
        return
    if relatorio.get('replicadas'):
        messages.success(request, 'Replicado para: ' + ', '.join(relatorio['replicadas']) + '.')
    if relatorio.get('ignoradas'):
        limite = relatorio['ignoradas'][:4]
        sufixo = '' if len(relatorio['ignoradas']) <= 4 else f' (+{len(relatorio["ignoradas"]) - 4} outra(s))'
        messages.warning(request, 'Nao foi possivel replicar para: ' + '; '.join(limite) + sufixo + '.')


def _shared_promocao_id(obj, prefixo):
    return ReplicacaoProdutoService._shared_id(obj, prefixo)


def _copia_replicada_existe(model, filial, id_externo):
    return bool(id_externo and model.objects.for_filial(filial).filter(id_externo=id_externo).exists())


def _produto_destino(produto, filial):
    if not produto:
        return None
    return ReplicacaoProdutoService._produto_destino(produto, filial)


def _categoria_destino(categoria, filial):
    if not categoria:
        return None
    return ReplicacaoProdutoService._categoria_destino(categoria, filial)


def _tem_duplicidade(linhas, chave_fn):
    vistos = set()
    for linha in linhas:
        chave = chave_fn(linha)
        if chave in vistos:
            return True
        vistos.add(chave)
    return False


def _linhas_preenchidas(formset, campos):
    linhas = []
    for form in formset:
        if not form.is_valid():
            return None
        cleaned = form.cleaned_data
        if any(cleaned.get(campo) not in (None, '') for campo in campos):
            linhas.append(cleaned)
    return linhas


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _periodo_ativo(obj, hoje):
    inicio = getattr(obj, 'data_inicio', None) or getattr(obj, 'promocao_inicio', None)
    fim = getattr(obj, 'data_fim', None) or getattr(obj, 'promocao_fim', None)
    if not inicio and not fim:
        return True
    if inicio and inicio > hoje:
        return False
    if fim and fim < hoje:
        return False
    return True


def _dias_semana_texto(dias):
    dias = dias or DIAS_SEMANA_TODOS
    selecionados = [dia for dia in dias.split(',') if dia in DIAS_SEMANA_LABELS]
    if len(selecionados) == 7:
        return 'Todos os dias'
    return ', '.join(DIAS_SEMANA_LABELS[dia] for dia in selecionados) or 'Todos os dias'


def _dias_semana_todos(dias):
    selecionados = {dia for dia in (dias or DIAS_SEMANA_TODOS).split(',') if dia in DIAS_SEMANA_LABELS}
    return selecionados == set(DIAS_SEMANA_LABELS)


def _validade_texto(inicio, fim, dias=None):
    dias_texto = _dias_semana_texto(dias)
    sufixo_dias = '' if _dias_semana_todos(dias) else f' - {dias_texto}'
    if not inicio and not fim:
        return f'Inicio imediato, sem prazo de termino{sufixo_dias}'
    return f'{_fmt_date(inicio) if inicio else "Inicio imediato"} ate {_fmt_date(fim) if fim else "sem prazo de termino"} - {dias_texto}'


def _fmt_date(valor):
    return valor.strftime('%d/%m/%Y') if valor else ''


def _status_promocao(obj, hoje):
    inicio = getattr(obj, 'data_inicio', None) or getattr(obj, 'promocao_inicio', None)
    fim = getattr(obj, 'data_fim', None) or getattr(obj, 'promocao_fim', None)
    ativo = getattr(obj, 'preco_promocional_ativo', getattr(obj, 'promocao_ativa', getattr(obj, 'ativo', True)))
    if fim and fim < hoje:
        return {
            'texto': 'Finalizada',
            'classe': 'warn',
            'estado': 'finalizadas',
            'finalizada': True,
            'programada': False,
            'tooltip': 'Promocao finalizada pela data. Ao editar, informe uma nova data e marque Ativo para ativar novamente.',
        }
    if ativo and inicio and inicio > hoje:
        return {
            'texto': 'Programado',
            'classe': 'info',
            'estado': 'programadas',
            'finalizada': False,
            'programada': True,
            'tooltip': 'Promocao programada para iniciar na data informada.',
        }
    if ativo:
        return {'texto': 'Ativo', 'classe': 'ok', 'estado': 'ativas', 'finalizada': False, 'programada': False, 'tooltip': ''}
    return {'texto': 'Inativo', 'classe': 'off', 'estado': 'inativas', 'finalizada': False, 'programada': False, 'tooltip': ''}


def _produto_label(produto):
    return produto.descricao_curta or produto.descricao or f'Produto {produto.pk}'


def _fmt_decimal(valor, casas=2):
    if valor in (None, ''):
        return '-'
    quant = Decimal('1') if casas == 0 else Decimal('1').scaleb(-casas)
    valor = Decimal(valor).quantize(quant, rounding=ROUND_HALF_UP)
    texto = f'{valor:f}'
    if casas == 0 and '.' in texto:
        texto = texto.rstrip('0').rstrip('.')
    return texto.replace('.', ',')


def _fmt_money(valor):
    return f'R$ {_fmt_decimal(valor, 2)}'


def _fmt_desconto(tipo, valor):
    if tipo == 'percentual':
        return f'{_fmt_decimal(valor, 2)}%'
    return _fmt_money(valor)


def _regra_preco_promocional(linha):
    tipo = linha.get('promocao_tipo_desconto') or 'preco_final'
    valor = linha.get('promocao_valor_desconto')
    preco_promocional = linha.get('preco_promocional') or Decimal('0')
    if tipo not in ('percentual', 'valor', 'preco_final'):
        tipo = 'preco_final'
    if valor is None:
        valor = preco_promocional
    return tipo, valor


def _preco_base_combo(promocao):
    return PrecoService.preco_base_combo(
        promocao.produto,
        usar_preco_promocional=promocao.usar_preco_promocional,
        filial=promocao.filial,
        validar_dia_semana=False,
        minimo_dias_semana=MIN_DIAS_PROMO_AUTOMATICA,
    )


def _valor_combo(promocao, faixa):
    qtd = faixa.quantidade_minima or Decimal('0')
    preco = _preco_base_combo(promocao)
    total = preco * qtd
    unitario = PrecoService.preco_unitario_combo(preco, faixa)
    total_combo = (unitario * qtd).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return total, total_combo, unitario


def _total_kit(kit):
    total = Decimal('0')
    for item in kit.itens.all():
        preco = PrecoService.preco_vivo_produto(
            item.produto,
            usar_preco_promocional=kit.permite_preco_promocional,
            filial=kit.filial,
            quantidade=item.quantidade or Decimal('0'),
            validar_dia_semana=False,
            minimo_dias_semana=MIN_DIAS_PROMO_AUTOMATICA,
        )
        total += preco * (item.quantidade or Decimal('0'))
    if kit.tipo_desconto == 'percentual':
        final = total * (Decimal('1') - (kit.valor_desconto or Decimal('0')) / Decimal('100'))
    elif kit.tipo_desconto == 'valor':
        final = total - (kit.valor_desconto or Decimal('0'))
    else:
        final = kit.valor_desconto or Decimal('0')
    if final < 0:
        final = Decimal('0')
    return total, final


def _preco_gatilho_brinde(brinde):
    return PrecoService.preco_vivo_produto(
        brinde.produto_gatilho,
        usar_preco_promocional=brinde.permite_preco_promocional,
        filial=brinde.filial,
        quantidade=brinde.quantidade_gatilho or Decimal('1'),
        validar_dia_semana=False,
        minimo_dias_semana=MIN_DIAS_PROMO_AUTOMATICA,
    )


def _total_brinde(brinde):
    total_brindes = Decimal('0')
    for item in brinde.itens.all():
        total_brindes += (item.produto.preco_venda or Decimal('0')) * (item.quantidade or Decimal('0'))
    return _preco_gatilho_brinde(brinde), total_brindes


def _ativos_unificados(promocoes, kits, brindes, descontos, produtos, hoje):
    itens = []
    for promo in promocoes:
        if not promo.ativo or not _periodo_ativo(promo, hoje):
            continue
        melhor = promo.faixas.order_by('quantidade_minima').last()
        itens.append({
            'tipo': 'Combo',
            'nome': promo.nome,
            'base': _fmt_money(_preco_base_combo(promo)),
            'regra': _fmt_money(_valor_combo(promo, melhor)[1]) if melhor else '-',
            'validade': _validade_texto(promo.data_inicio, promo.data_fim, promo.dias_semana),
            'replica': promo.replicar_filiais,
        })
    for kit in kits:
        if not kit.ativo or not _periodo_ativo(kit, hoje):
            continue
        total_kit, final_kit = _total_kit(kit)
        itens.append({
            'tipo': 'Kit',
            'nome': kit.nome,
            'base': _fmt_money(total_kit),
            'regra': _fmt_money(final_kit),
            'validade': _validade_texto(kit.data_inicio, kit.data_fim, kit.dias_semana),
            'replica': kit.replicar_filiais,
        })
    for brinde in brindes:
        if not brinde.ativo or not _periodo_ativo(brinde, hoje):
            continue
        preco_gatilho, total_brindes = _total_brinde(brinde)
        itens.append({
            'tipo': 'Brinde',
            'nome': brinde.nome,
            'base': _fmt_money(preco_gatilho),
            'regra': _fmt_money(total_brindes),
            'validade': _validade_texto(brinde.data_inicio, brinde.data_fim, brinde.dias_semana),
            'replica': brinde.replicar_filiais,
        })
    for desconto in descontos:
        if not desconto.ativo or not _periodo_ativo(desconto, hoje):
            continue
        regras = list(desconto.regras.all()[:3])
        itens.append({
            'tipo': 'Desconto por categoria',
            'nome': desconto.nome,
            'base': 'Categorias',
            'regra': ', '.join(
                f'{regra.get_tipo_desconto_display()}: {_fmt_desconto(regra.tipo_desconto, regra.valor_desconto)}'
                for regra in regras
            ) or '-',
            'validade': _validade_texto(desconto.data_inicio, desconto.data_fim, desconto.dias_semana),
            'replica': desconto.replicar_filiais,
        })
    for produto in produtos:
        itens.append({
            'tipo': 'Preco promocional',
            'nome': _produto_label(produto),
            'base': _fmt_money(produto.preco_venda),
            'regra': _fmt_money(PrecoService.calcular_preco_promocional(produto, filial=produto.filial)),
            'validade': _validade_texto(produto.promocao_inicio, produto.promocao_fim, produto.promocao_dias_semana),
            'replica': False,
        })
    return itens


def _replicar_promocao_quantidade(promocao, filiais=None):
    relatorio = _relatorio_replicacao()
    if not promocao.replicar_filiais:
        return relatorio
    id_externo = _shared_promocao_id(promocao, 'promo-quantidade')
    faixas = list(promocao.faixas.all())
    for filial in filiais if filiais is not None else _filiais_destino(promocao.filial, 'replicar_produtos_basicos'):
        if _copia_replicada_existe(PromocaoQuantidade, filial, id_externo):
            _registrar_ignorada(relatorio, filial, 'ja existe uma copia independente')
            continue
        produto_destino = _produto_destino(promocao.produto, filial)
        if not produto_destino:
            _registrar_ignorada(relatorio, filial, 'produto nao encontrado')
            continue
        destino = PromocaoQuantidade.objects.create(
            filial=filial,
            produto=produto_destino,
            nome=promocao.nome,
            id_externo=id_externo,
            data_inicio=promocao.data_inicio,
            data_fim=promocao.data_fim,
            dias_semana=promocao.dias_semana,
            usar_preco_promocional=promocao.usar_preco_promocional,
            replicar_filiais=False,
            ativo=promocao.ativo,
        )
        for faixa in faixas:
            PromocaoQuantidadeFaixa.objects.create(
                promocao=destino,
                condicao_quantidade=faixa.condicao_quantidade,
                quantidade_minima=faixa.quantidade_minima,
                tipo_desconto=faixa.tipo_desconto,
                valor=faixa.valor,
            )
        _registrar_replicada(relatorio, filial)
    return relatorio


def _replicar_kit_produto(kit, filiais=None):
    relatorio = _relatorio_replicacao()
    if not kit.replicar_filiais:
        return relatorio
    id_externo = _shared_promocao_id(kit, 'kit-produto')
    itens = list(kit.itens.all())
    for filial in filiais if filiais is not None else _filiais_destino(kit.filial, 'replicar_produtos_basicos'):
        if _copia_replicada_existe(KitProduto, filial, id_externo):
            _registrar_ignorada(relatorio, filial, 'ja existe uma copia independente')
            continue
        itens_destino = []
        for item in itens:
            produto_destino = _produto_destino(item.produto, filial)
            if not produto_destino:
                itens_destino = []
                break
            itens_destino.append((produto_destino, item.quantidade))
        if not itens_destino:
            _registrar_ignorada(relatorio, filial, 'um ou mais produtos nao foram encontrados')
            continue
        destino = KitProduto.objects.create(
            filial=filial,
            nome=kit.nome,
            id_externo=id_externo,
            descricao=kit.descricao,
            tipo_desconto=kit.tipo_desconto,
            valor_desconto=kit.valor_desconto,
            data_inicio=kit.data_inicio,
            data_fim=kit.data_fim,
            dias_semana=kit.dias_semana,
            replicar_filiais=False,
            permite_preco_promocional=kit.permite_preco_promocional,
            ativo=kit.ativo,
        )
        for produto_destino, quantidade in itens_destino:
            KitProdutoItem.objects.create(kit=destino, produto=produto_destino, quantidade=quantidade)
        _registrar_replicada(relatorio, filial)
    return relatorio


def _replicar_brinde_produto(brinde, filiais=None):
    relatorio = _relatorio_replicacao()
    if not brinde.replicar_filiais:
        return relatorio
    id_externo = _shared_promocao_id(brinde, 'brinde-produto')
    itens = list(brinde.itens.all())
    for filial in filiais if filiais is not None else _filiais_destino(brinde.filial, 'replicar_produtos_basicos'):
        if _copia_replicada_existe(BrindeProduto, filial, id_externo):
            _registrar_ignorada(relatorio, filial, 'ja existe uma copia independente')
            continue
        gatilho_destino = _produto_destino(brinde.produto_gatilho, filial)
        if not gatilho_destino:
            _registrar_ignorada(relatorio, filial, 'produto gerador nao encontrado')
            continue
        itens_destino = []
        for item in itens:
            produto_destino = _produto_destino(item.produto, filial)
            if not produto_destino:
                itens_destino = []
                break
            itens_destino.append((produto_destino, item.quantidade))
        if not itens_destino:
            _registrar_ignorada(relatorio, filial, 'um ou mais brindes nao foram encontrados')
            continue
        destino = BrindeProduto.objects.create(
            filial=filial,
            nome=brinde.nome,
            id_externo=id_externo,
            descricao=brinde.descricao,
            produto_gatilho=gatilho_destino,
            quantidade_gatilho=brinde.quantidade_gatilho,
            data_inicio=brinde.data_inicio,
            data_fim=brinde.data_fim,
            dias_semana=brinde.dias_semana,
            replicar_filiais=False,
            permite_preco_promocional=brinde.permite_preco_promocional,
            ativo=brinde.ativo,
        )
        for produto_destino, quantidade in itens_destino:
            BrindeProdutoItem.objects.create(brinde=destino, produto=produto_destino, quantidade=quantidade)
        _registrar_replicada(relatorio, filial)
    return relatorio


def _replicar_kit_categoria(kit, filiais=None):
    relatorio = _relatorio_replicacao()
    if not kit.replicar_filiais:
        return relatorio
    id_externo = _shared_promocao_id(kit, 'kit-categoria')
    regras = list(kit.regras.all())
    for filial in filiais if filiais is not None else _filiais_destino(kit.filial, 'replicar_categorias'):
        if _copia_replicada_existe(KitCategoria, filial, id_externo):
            _registrar_ignorada(relatorio, filial, 'ja existe uma copia independente')
            continue
        regras_destino = []
        for regra in regras:
            categoria_destino = _categoria_destino(regra.categoria, filial) if regra.categoria_id else None
            subcategoria_destino = _categoria_destino(regra.subcategoria, filial) if regra.subcategoria_id else None
            if regra.categoria_id and not categoria_destino:
                regras_destino = []
                break
            if regra.subcategoria_id and not subcategoria_destino:
                regras_destino = []
                break
            regras_destino.append((regra, categoria_destino, subcategoria_destino))
        if not regras_destino:
            _registrar_ignorada(relatorio, filial, 'categoria ou subcategoria nao encontrada')
            continue
        destino = KitCategoria.objects.create(
            filial=filial,
            nome=kit.nome,
            id_externo=id_externo,
            descricao=kit.descricao,
            tipo_desconto=kit.tipo_desconto,
            valor_desconto=kit.valor_desconto,
            data_inicio=kit.data_inicio,
            data_fim=kit.data_fim,
            dias_semana=kit.dias_semana,
            replicar_filiais=False,
            permite_preco_promocional=kit.permite_preco_promocional,
            ativo=kit.ativo,
        )
        for regra, categoria_destino, subcategoria_destino in regras_destino:
            KitCategoriaRegra.objects.create(
                kit=destino,
                categoria=categoria_destino,
                subcategoria=subcategoria_destino,
                quantidade_minima=regra.quantidade_minima,
                tipo_desconto=regra.tipo_desconto,
                valor_desconto=regra.valor_desconto,
            )
        _registrar_replicada(relatorio, filial)
    return relatorio


def _produtos_promocao_destino(produto, filiais=None, relatorio=None):
    destinos = []
    for filial in filiais if filiais is not None else _filiais_destino(produto.filial, 'replicar_preco_venda'):
        qs = Produto.objects.for_filial(filial)
        destino = None
        if produto.id_externo:
            destino = qs.filter(id_externo=produto.id_externo).first()
        if not destino and produto.codigo:
            destino = qs.filter(codigo=produto.codigo).first()
        if not destino and produto.codigo_barras:
            destino = qs.filter(codigo_barras=produto.codigo_barras).first()
        if destino:
            destinos.append((destino, filial))
            if relatorio is not None:
                _registrar_replicada(relatorio, filial)
        elif relatorio is not None:
            _registrar_ignorada(relatorio, filial, 'produto nao encontrado')
    return destinos


def _aplicar_promocao_filial(produto, filial):
    promocao = PrecoService.promocao_produto_contexto(produto, filial)
    produto.preco_promocional_vivo = PrecoService.calcular_preco_promocional(produto, filial=filial)
    produto.preco_promocional_ativo = getattr(promocao, 'preco_promocional_ativo', True)
    produto.preco_promocional_replicar_filiais = getattr(promocao, 'preco_promocional_replicar_filiais', False)
    produto.promocao_tipo_desconto = promocao.promocao_tipo_desconto
    produto.promocao_valor_desconto = promocao.promocao_valor_desconto
    produto.promocao_inicio = promocao.promocao_inicio
    produto.promocao_fim = promocao.promocao_fim
    produto.promocao_dias_semana = promocao.promocao_dias_semana
    return produto


def _kit_categoria_tem_preco_promocional_vivo(kit, produtos, filial):
    if not getattr(kit, 'permite_preco_promocional', False):
        return False
    regras = list(kit.regras.all())
    for regra in regras:
        quantidade = regra.quantidade_minima or Decimal('1')
        for produto in produtos:
            if not PrecoService.regra_categoria_aplica(regra, produto, quantidade):
                continue
            preco_promocional = PrecoService.preco_promocional_vigente(
                produto,
                filial=filial,
                validar_dia_semana=False,
                minimo_dias_semana=MIN_DIAS_PROMO_AUTOMATICA,
            )
            if preco_promocional is not None and preco_promocional < (produto.preco_venda or Decimal('0')):
                return True
    return False


def _atualizar_preco_promocional(produto, linha, filial=None, replicar_filiais=False):
    tipo, valor = _regra_preco_promocional(linha)
    preco_promocional = PrecoService.aplicar_regra_desconto(produto.preco_venda, tipo, valor)
    dados = {
        'preco_promocional': preco_promocional,
        'preco_promocional_ativo': bool(linha.get('ativo', True)),
        'preco_promocional_replicar_filiais': bool(replicar_filiais),
        'promocao_tipo_desconto': tipo,
        'promocao_valor_desconto': valor,
        'promocao_inicio': linha.get('promocao_inicio'),
        'promocao_fim': linha.get('promocao_fim'),
        'promocao_dias_semana': linha.get('promocao_dias_semana') or DIAS_SEMANA_TODOS,
    }
    if filial:
        vinculo, _ = ProdutoFilial.objects.get_or_create(
            produto=produto,
            filial=filial,
            defaults={'ativo': True},
        )
        for campo, valor_campo in dados.items():
            setattr(vinculo, campo, valor_campo)
        vinculo.ativo = True
        vinculo.save(update_fields=[*dados.keys(), 'ativo', 'updated_at'])
        return vinculo

    dados_produto = {
        campo: valor_campo for campo, valor_campo in dados.items()
        if campo not in {'preco_promocional_ativo', 'preco_promocional_replicar_filiais'}
    }
    for campo, valor_campo in dados_produto.items():
        setattr(produto, campo, valor_campo)
    produto.save(update_fields=[*dados_produto.keys(), 'updated_at'])
    return produto


class ComboPromocaoListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    template_name = 'produtos/promocao/list.html'

    def _exige_permissao(self, request, acao):
        if request.user.tem_permissao('produtos', acao):
            return True
        messages.error(request, f'Voce nao tem permissao de "{acao}" no modulo "produtos".')
        return False

    def _acao_gravacao(self, request, acao):
        if acao == 'promocao_quantidade':
            return 'editar' if _as_int(request.POST.get('promo_id')) else 'criar'
        if acao == 'kit_produto':
            return 'editar' if _as_int(request.POST.get('kit_id')) else 'criar'
        if acao == 'brinde_produto':
            return 'editar' if _as_int(request.POST.get('brinde_id')) else 'criar'
        if acao == 'kit_categoria':
            return 'editar' if _as_int(request.POST.get('kitcat_id')) else 'criar'
        if acao in {'preco_promocional', 'toggle_promocao_quantidade', 'toggle_kit_produto', 'toggle_brinde_produto', 'toggle_kit_categoria', 'toggle_preco_promocional', 'limpar_preco_promocional'}:
            return 'editar'
        return None

    def _context(self, request, **forms):
        hoje = timezone.localdate()
        produtos_promocionais = list(
            Produto.objects.for_filial(request.filial_ativa)
            .select_related('categoria__categoria_pai', 'subcategoria__categoria_pai')
            .order_by('descricao')
        )
        produtos_base = (
            Produto.objects.for_filial(request.filial_ativa)
            .filter(ativo=True)
            .select_related('categoria__categoria_pai', 'subcategoria__categoria_pai')
            .order_by('descricao')
        )
        promocoes_quantidade = list(
            PromocaoQuantidade.objects.for_filial(request.filial_ativa)
            .select_related('produto__categoria__categoria_pai', 'produto__subcategoria__categoria_pai')
            .prefetch_related('faixas')
        )
        for promocao in promocoes_quantidade:
            produto_preco_base = promocao.produto.preco_venda or Decimal('0')
            produto_preco_info = PrecoService.melhor_preco_produto_detalhado(
                promocao.produto,
                filial=request.filial_ativa,
                validar_dia_semana=False,
                minimo_dias_semana=MIN_DIAS_PROMO_AUTOMATICA,
            )
            produto_preco_promocional = produto_preco_info['preco']
            produto_tem_preco_promocional = (
                produto_preco_promocional < produto_preco_base
                or PrecoService.produto_tem_promocao_vigente(
                    promocao.produto,
                    filial=request.filial_ativa,
                    validar_dia_semana=False,
                    minimo_dias_semana=MIN_DIAS_PROMO_AUTOMATICA,
                )
            )
            promocao.produto_label = _produto_label(promocao.produto)
            promocao.produto_preco_promocional_vivo = produto_preco_promocional
            promocao.produto_preco_promocional_origem = produto_preco_info['origem']
            promocao.produto_preco_promocional_detalhe = produto_preco_info['detalhe']
            promocao.produto_tem_preco_promocional_vivo = produto_tem_preco_promocional
            promocao.dias_semana_texto = _dias_semana_texto(promocao.dias_semana)
            promocao.validade_texto = _validade_texto(promocao.data_inicio, promocao.data_fim, promocao.dias_semana)
            promocao.status_info = _status_promocao(promocao, hoje)
            promocao.active_state = promocao.status_info['estado']
            promocao.faixas_display = []
            for faixa in promocao.faixas.all():
                total_normal, total_combo, unitario_combo = _valor_combo(promocao, faixa)
                promocao.faixas_display.append({
                    'condicao': faixa.get_condicao_quantidade_display(),
                    'quantidade': _fmt_decimal(faixa.quantidade_minima, 0 if faixa.quantidade_minima == faixa.quantidade_minima.to_integral_value() else 3),
                    'desconto': _fmt_desconto(faixa.tipo_desconto, faixa.valor),
                    'preco_unitario_normal': _fmt_money(_preco_base_combo(promocao)),
                    'preco_unitario_original': _fmt_money(promocao.produto.preco_venda),
                    'valor_total_normal': _fmt_money(total_normal),
                    'usa_preco_promocional': promocao.usar_preco_promocional and produto_tem_preco_promocional,
                    'preco_unitario_combo': _fmt_money(unitario_combo),
                    'valor_total': _fmt_money(total_combo),
                })
        kits_produtos = list(
            KitProduto.objects.for_filial(request.filial_ativa)
            .prefetch_related('itens__produto__categoria__categoria_pai', 'itens__produto__subcategoria__categoria_pai')
        )
        for kit in kits_produtos:
            kit.dias_semana_texto = _dias_semana_texto(kit.dias_semana)
            kit.validade_texto = _validade_texto(kit.data_inicio, kit.data_fim, kit.dias_semana)
            kit.status_info = _status_promocao(kit, hoje)
            kit.active_state = kit.status_info['estado']
            total_kit, valor_final_kit = _total_kit(kit)
            kit.total_normal_texto = _fmt_money(total_kit)
            kit.valor_final_texto = _fmt_money(valor_final_kit)
            kit.desconto_aplicado_texto = _fmt_money(max(Decimal('0'), total_kit - valor_final_kit))
            kit.tipo_desconto_texto = {
                'percentual': 'Desconto em %',
                'valor': 'Desconto em R$',
                'preco_final': 'Definir valor final',
            }.get(kit.tipo_desconto, kit.get_tipo_desconto_display())
            kit.valor_desconto_texto = _fmt_desconto(kit.tipo_desconto, kit.valor_desconto)
            kit.tem_preco_promocional_vivo = False
            for item in kit.itens.all():
                item_preco_base = item.produto.preco_venda or Decimal('0')
                item_preco_info = PrecoService.melhor_preco_produto_detalhado(
                    item.produto,
                    filial=request.filial_ativa,
                    validar_dia_semana=False,
                    minimo_dias_semana=MIN_DIAS_PROMO_AUTOMATICA,
                )
                item.preco_promocional_vivo = item_preco_info['preco']
                item.preco_promocional_origem = item_preco_info['origem']
                item.preco_promocional_detalhe = item_preco_info['detalhe']
                item.tem_preco_promocional_vivo = (
                    item.preco_promocional_vivo < item_preco_base
                    or PrecoService.produto_tem_promocao_vigente(
                        item.produto,
                        filial=request.filial_ativa,
                        validar_dia_semana=False,
                        minimo_dias_semana=MIN_DIAS_PROMO_AUTOMATICA,
                    )
                )
                kit.tem_preco_promocional_vivo = kit.tem_preco_promocional_vivo or item.tem_preco_promocional_vivo
        brindes_produtos = list(
            BrindeProduto.objects.for_filial(request.filial_ativa)
            .select_related('produto_gatilho__categoria__categoria_pai', 'produto_gatilho__subcategoria__categoria_pai')
            .prefetch_related('itens__produto__categoria__categoria_pai', 'itens__produto__subcategoria__categoria_pai')
        )
        for brinde in brindes_produtos:
            brinde.produto_gatilho_label = _produto_label(brinde.produto_gatilho)
            brinde.dias_semana_texto = _dias_semana_texto(brinde.dias_semana)
            brinde.validade_texto = _validade_texto(brinde.data_inicio, brinde.data_fim, brinde.dias_semana)
            brinde.status_info = _status_promocao(brinde, hoje)
            brinde.active_state = brinde.status_info['estado']
            gatilho_base = brinde.produto_gatilho.preco_venda or Decimal('0')
            gatilho_info = PrecoService.melhor_preco_produto_detalhado(
                brinde.produto_gatilho,
                filial=request.filial_ativa,
                quantidade=brinde.quantidade_gatilho or Decimal('1'),
                validar_dia_semana=False,
                minimo_dias_semana=MIN_DIAS_PROMO_AUTOMATICA,
            )
            brinde.gatilho_preco_promocional_vivo = gatilho_info['preco']
            brinde.gatilho_preco_promocional_origem = gatilho_info['origem']
            brinde.gatilho_preco_promocional_detalhe = gatilho_info['detalhe']
            brinde.gatilho_tem_preco_promocional_vivo = (
                brinde.gatilho_preco_promocional_vivo < gatilho_base
                or PrecoService.produto_tem_promocao_vigente(
                    brinde.produto_gatilho,
                    filial=request.filial_ativa,
                    validar_dia_semana=False,
                    minimo_dias_semana=MIN_DIAS_PROMO_AUTOMATICA,
                )
            )
            brinde.gatilho_preco_base_texto = _fmt_money(gatilho_base)
            brinde.gatilho_preco_usado = _preco_gatilho_brinde(brinde)
            brinde.gatilho_preco_usado_texto = _fmt_money(brinde.gatilho_preco_usado)
            brinde.quantidade_gatilho_texto = _fmt_decimal(
                brinde.quantidade_gatilho,
                0 if brinde.quantidade_gatilho == brinde.quantidade_gatilho.to_integral_value() else 3,
            )
            total_brindes = Decimal('0')
            for item in brinde.itens.all():
                item.produto_label = _produto_label(item.produto)
                item.valor_unitario = item.produto.preco_venda or Decimal('0')
                item.valor_total = item.valor_unitario * (item.quantidade or Decimal('0'))
                item.valor_unitario_texto = _fmt_money(item.valor_unitario)
                item.valor_total_texto = _fmt_money(item.valor_total)
                total_brindes += item.valor_total
            brinde.valor_brinde = total_brindes
            brinde.valor_brinde_texto = _fmt_money(total_brindes)
            brinde.desconto_aplicado_texto = _fmt_money(total_brindes)
        kits_categorias = list(KitCategoria.objects.for_filial(request.filial_ativa).prefetch_related('regras__categoria', 'regras__subcategoria'))
        for kit in kits_categorias:
            kit.dias_semana_texto = _dias_semana_texto(kit.dias_semana)
            kit.validade_texto = _validade_texto(kit.data_inicio, kit.data_fim, kit.dias_semana)
            kit.status_info = _status_promocao(kit, hoje)
            kit.active_state = kit.status_info['estado']
            kit.tem_preco_promocional_vivo = _kit_categoria_tem_preco_promocional_vivo(
                kit,
                produtos_promocionais,
                request.filial_ativa,
            )
            kit.regras_display = []
            for regra in kit.regras.all():
                alvo = regra.categoria.nome if regra.categoria else 'Todas as categorias'
                if regra.subcategoria:
                    alvo = f'{alvo} / {regra.subcategoria.nome}'
                kit.regras_display.append({
                    'quantidade': _fmt_decimal(regra.quantidade_minima, 0 if regra.quantidade_minima == regra.quantidade_minima.to_integral_value() else 3),
                    'alvo': alvo,
                    'desconto': _fmt_desconto(regra.tipo_desconto, regra.valor_desconto),
                })
        produtos_promocionais_filtrados = []
        for produto in produtos_promocionais:
            promocao_produto = PrecoService.promocao_produto_contexto(produto, request.filial_ativa)
            preco_promocional = promocao_produto.preco_promocional or Decimal('0')
            promocao_ativa = getattr(promocao_produto, 'preco_promocional_ativo', True)
            if preco_promocional > 0 or not promocao_ativa:
                produtos_promocionais_filtrados.append(_aplicar_promocao_filial(produto, request.filial_ativa))
        produtos_promocionais = produtos_promocionais_filtrados
        for produto in produtos_promocionais:
            produto.promocao_label = _produto_label(produto)
            produto.promocao_regra_texto = _fmt_desconto(produto.promocao_tipo_desconto, produto.promocao_valor_desconto)
            produto.validade_inicio_texto = _fmt_date(produto.promocao_inicio) or 'Inicio imediato'
            produto.validade_fim_texto = _fmt_date(produto.promocao_fim) or 'Sem prazo de termino'
            produto.dias_semana_texto = _dias_semana_texto(produto.promocao_dias_semana)
            produto.validade_texto = _validade_texto(produto.promocao_inicio, produto.promocao_fim, produto.promocao_dias_semana)
            produto.status_info = _status_promocao(produto, hoje)
            produto.active_state = produto.status_info['estado']
        promocoes_quantidade_ativas = [promo for promo in promocoes_quantidade if promo.ativo and _periodo_ativo(promo, hoje)]
        kits_produtos_ativos = [kit for kit in kits_produtos if kit.ativo and _periodo_ativo(kit, hoje)]
        brindes_produtos_ativos = [brinde for brinde in brindes_produtos if brinde.ativo and _periodo_ativo(brinde, hoje)]
        kits_categorias_ativos = [kit for kit in kits_categorias if kit.ativo and _periodo_ativo(kit, hoje)]
        produtos_promocionais_ativos = [
            produto for produto in produtos_promocionais
            if produto.preco_promocional_ativo and _periodo_ativo(produto, hoje)
        ]
        promocoes_quantidade_finalizadas = [promo for promo in promocoes_quantidade if promo.status_info['finalizada']]
        kits_produtos_finalizados = [kit for kit in kits_produtos if kit.status_info['finalizada']]
        brindes_produtos_finalizados = [brinde for brinde in brindes_produtos if brinde.status_info['finalizada']]
        kits_categorias_finalizados = [kit for kit in kits_categorias if kit.status_info['finalizada']]
        produtos_promocionais_finalizados = [produto for produto in produtos_promocionais if produto.status_info['finalizada']]
        promocoes_quantidade_programadas = [promo for promo in promocoes_quantidade if promo.status_info['programada']]
        kits_produtos_programados = [kit for kit in kits_produtos if kit.status_info['programada']]
        brindes_produtos_programados = [brinde for brinde in brindes_produtos if brinde.status_info['programada']]
        kits_categorias_programados = [kit for kit in kits_categorias if kit.status_info['programada']]
        produtos_promocionais_programados = [produto for produto in produtos_promocionais if produto.status_info['programada']]
        promocoes_quantidade_inativas = [promo for promo in promocoes_quantidade if promo.status_info['estado'] == 'inativas']
        kits_produtos_inativos = [kit for kit in kits_produtos if kit.status_info['estado'] == 'inativas']
        brindes_produtos_inativos = [brinde for brinde in brindes_produtos if brinde.status_info['estado'] == 'inativas']
        kits_categorias_inativos = [kit for kit in kits_categorias if kit.status_info['estado'] == 'inativas']
        produtos_promocionais_inativos = [produto for produto in produtos_promocionais if produto.status_info['estado'] == 'inativas']
        promocoes_quantidade_inicio = promocoes_quantidade_ativas + promocoes_quantidade_programadas + promocoes_quantidade_finalizadas + promocoes_quantidade_inativas
        kits_produtos_inicio = kits_produtos_ativos + kits_produtos_programados + kits_produtos_finalizados + kits_produtos_inativos
        brindes_produtos_inicio = brindes_produtos_ativos + brindes_produtos_programados + brindes_produtos_finalizados + brindes_produtos_inativos
        kits_categorias_inicio = kits_categorias_ativos + kits_categorias_programados + kits_categorias_finalizados + kits_categorias_inativos
        produtos_promocionais_inicio = produtos_promocionais_ativos + produtos_promocionais_programados + produtos_promocionais_finalizados + produtos_promocionais_inativos
        produtos_promocao_data = []
        for produto in produtos_base:
            promocao_produto = PrecoService.promocao_produto_contexto(produto, request.filial_ativa)
            preco_info = PrecoService.melhor_preco_produto_detalhado(
                produto,
                filial=request.filial_ativa,
                validar_dia_semana=False,
                minimo_dias_semana=MIN_DIAS_PROMO_AUTOMATICA,
            )
            produtos_promocao_data.append({
                'id': produto.pk,
                'preco': str(produto.preco_venda or 0),
                'preco_promocional': str(preco_info['preco']),
                'promocao_origem': preco_info['origem'],
                'promocao_detalhe': preco_info['detalhe'],
                'promocao_tipo_desconto': promocao_produto.promocao_tipo_desconto,
                'promocao_valor_desconto': str(promocao_produto.promocao_valor_desconto or 0),
                'promocao_ativa': (
                    preco_info['preco'] < (produto.preco_venda or Decimal('0'))
                    or PrecoService.produto_tem_promocao_vigente(
                        produto,
                        filial=request.filial_ativa,
                        validar_dia_semana=False,
                        minimo_dias_semana=MIN_DIAS_PROMO_AUTOMATICA,
                    )
                ),
                'categoria_id': produto.categoria_id,
                'subcategoria_id': produto.subcategoria_id,
            })
        context = {
            'promocao_form': forms.get('promocao_form') or PromocaoQuantidadeForm(filial=request.filial_ativa, prefix='promo'),
            'faixa_formset': forms.get('faixa_formset') or PromocaoQuantidadeFaixaFormSet(prefix='faixa'),
            'kit_form': forms.get('kit_form') or KitProdutoForm(prefix='kit'),
            'kit_item_formset': forms.get('kit_item_formset') or KitProdutoItemFormSet(prefix='kititem', form_kwargs={'filial': request.filial_ativa}),
            'brinde_form': forms.get('brinde_form') or BrindeProdutoForm(filial=request.filial_ativa, prefix='brinde'),
            'brinde_item_formset': forms.get('brinde_item_formset') or BrindeProdutoItemFormSet(prefix='brindeitem', form_kwargs={'filial': request.filial_ativa}),
            'kit_categoria_form': forms.get('kit_categoria_form') or KitCategoriaForm(prefix='kitcat'),
            'kit_categoria_regra_formset': forms.get('kit_categoria_regra_formset') or KitCategoriaRegraFormSet(prefix='kitcatregra', form_kwargs={'filial': request.filial_ativa}),
            'preco_promocional_formset': forms.get('preco_promocional_formset') or PrecoPromocionalItemFormSet(prefix='precoitem', form_kwargs={'filial': request.filial_ativa}),
            'promocoes_quantidade': promocoes_quantidade,
            'promocoes_quantidade_ativas': promocoes_quantidade_ativas,
            'promocoes_quantidade_inicio': promocoes_quantidade_inicio,
            'kits_produtos': kits_produtos,
            'kits_produtos_ativos': kits_produtos_ativos,
            'kits_produtos_inicio': kits_produtos_inicio,
            'brindes_produtos': brindes_produtos,
            'brindes_produtos_ativos': brindes_produtos_ativos,
            'brindes_produtos_inicio': brindes_produtos_inicio,
            'kits_categorias': kits_categorias,
            'kits_categorias_ativos': kits_categorias_ativos,
            'kits_categorias_inicio': kits_categorias_inicio,
            'produtos_promocionais': produtos_promocionais,
            'produtos_promocionais_ativos': produtos_promocionais_ativos,
            'promocoes_quantidade_inativas': promocoes_quantidade_inativas,
            'kits_produtos_inativos': kits_produtos_inativos,
            'brindes_produtos_inativos': brindes_produtos_inativos,
            'kits_categorias_inativos': kits_categorias_inativos,
            'produtos_promocionais_inativos': produtos_promocionais_inativos,
            'produtos_promocionais_inicio': produtos_promocionais_inicio,
            'active_panel': forms.get('active_panel') or request.GET.get('aba') or 'inicio',
            'produtos_promocao_json': produtos_promocao_data,
            'replicacao_filiais_produtos': _filiais_replicacao_context(request.filial_ativa, 'replicar_produtos_basicos'),
            'replicacao_filiais_categorias': _filiais_replicacao_context(request.filial_ativa, 'replicar_categorias'),
            'replicacao_filiais_precos': _filiais_replicacao_context(request.filial_ativa, 'replicar_preco_venda'),
        }
        try:
            context.update(promocao_log_context(request))
        except Exception:
            logger.exception('Falha ao montar log da tela de promocoes.')
        return context

    def get(self, request):
        return render(request, self.template_name, self._context(request))

    def post(self, request):
        acao = request.POST.get('acao')
        permissao = self._acao_gravacao(request, acao)
        if permissao and not self._exige_permissao(request, permissao):
            return redirect(reverse('produtos:combo-promocao-list'))
        if acao == 'promocao_quantidade':
            return self._criar_promocao_quantidade(request)
        if acao == 'kit_produto':
            return self._criar_kit_produto(request)
        if acao == 'brinde_produto':
            return self._criar_brinde_produto(request)
        if acao == 'kit_categoria':
            return self._criar_kit_categoria(request)
        if acao == 'preco_promocional':
            return self._salvar_precos_promocionais(request)
        if acao == 'toggle_promocao_quantidade':
            return self._toggle_model(request, PromocaoQuantidade, 'quantidade')
        if acao == 'toggle_kit_produto':
            return self._toggle_model(request, KitProduto, 'kit')
        if acao == 'toggle_brinde_produto':
            return self._toggle_model(request, BrindeProduto, 'brinde')
        if acao == 'toggle_kit_categoria':
            return self._toggle_model(request, KitCategoria, 'categoria')
        if acao == 'toggle_preco_promocional':
            return self._toggle_preco_promocional(request)
        if acao == 'limpar_preco_promocional':
            return self._limpar_preco_promocional(request)
        messages.error(request, 'Acao invalida.')
        return redirect(reverse('produtos:combo-promocao-list'))

    def _toggle_model(self, request, model, painel):
        obj_id = _as_int(request.POST.get('id'))
        ativo = request.POST.get('ativo') == '1'
        obj = model.objects.for_filial(request.filial_ativa).filter(pk=obj_id).first()
        if not obj:
            messages.error(request, 'Registro nao encontrado para esta filial.')
            return redirect(reverse('produtos:combo-promocao-list'))
        obj.ativo = ativo
        obj.save(update_fields=['ativo', 'updated_at'])
        messages.success(request, 'Status atualizado.')
        return redirect(f"{reverse('produtos:combo-promocao-list')}?aba={painel}")

    def _toggle_preco_promocional(self, request):
        produto = Produto.objects.for_filial(request.filial_ativa).filter(pk=_as_int(request.POST.get('id'))).first()
        if not produto:
            messages.error(request, 'Produto nao encontrado para esta filial.')
            return redirect(reverse('produtos:combo-promocao-list'))
        ativo = request.POST.get('ativo') == '1'
        vinculo = ProdutoFilial.objects.filter(
            produto=produto,
            filial=request.filial_ativa,
            ativo=True,
        ).first()
        alvo = vinculo or produto
        if hasattr(alvo, 'preco_promocional_ativo'):
            alvo.preco_promocional_ativo = ativo
            alvo.save(update_fields=['preco_promocional_ativo', 'updated_at'])
        elif not ativo:
            alvo.preco_promocional = 0
            alvo.promocao_tipo_desconto = 'preco_final'
            alvo.promocao_valor_desconto = 0
            alvo.promocao_inicio = None
            alvo.promocao_fim = None
            alvo.promocao_dias_semana = DIAS_SEMANA_TODOS
            alvo.save(update_fields=[
                'preco_promocional',
                'promocao_tipo_desconto',
                'promocao_valor_desconto',
                'promocao_inicio',
                'promocao_fim',
                'promocao_dias_semana',
                'updated_at',
            ])
        else:
            messages.error(request, 'Nao foi possivel ativar este preco promocional legado.')
            return redirect(f"{reverse('produtos:combo-promocao-list')}?aba=precos")
        if ativo and not produto.ativo:
            messages.success(request, 'Preco promocional ativado. Produto segue marcado como inativo.')
        else:
            messages.success(request, f'Preco promocional {"ativado" if ativo else "inativado"}.')
        return redirect(f"{reverse('produtos:combo-promocao-list')}?aba=precos")

    def _limpar_preco_promocional(self, request):
        produto = Produto.objects.for_filial(request.filial_ativa).filter(pk=_as_int(request.POST.get('id'))).first()
        if not produto:
            messages.error(request, 'Produto nao encontrado para esta filial.')
            return redirect(reverse('produtos:combo-promocao-list'))
        vinculo = ProdutoFilial.objects.filter(
            produto=produto,
            filial=request.filial_ativa,
            ativo=True,
        ).first()
        alvo = vinculo or produto
        if hasattr(alvo, 'preco_promocional_ativo'):
            alvo.preco_promocional_ativo = False
            alvo.save(update_fields=[
                'preco_promocional_ativo',
                'updated_at',
            ])
        else:
            alvo.preco_promocional = 0
            alvo.promocao_tipo_desconto = 'preco_final'
            alvo.promocao_valor_desconto = 0
            alvo.promocao_inicio = None
            alvo.promocao_fim = None
            alvo.promocao_dias_semana = DIAS_SEMANA_TODOS
            alvo.save(update_fields=[
                'preco_promocional',
                'promocao_tipo_desconto',
                'promocao_valor_desconto',
                'promocao_inicio',
                'promocao_fim',
                'promocao_dias_semana',
                'updated_at',
            ])
        messages.success(request, 'Preco promocional inativado.')
        return redirect(f"{reverse('produtos:combo-promocao-list')}?aba=precos")

    def _criar_promocao_quantidade(self, request):
        promocao = PromocaoQuantidade.objects.for_filial(request.filial_ativa).filter(pk=_as_int(request.POST.get('promo_id'))).first()
        form = PromocaoQuantidadeForm(request.POST, filial=request.filial_ativa, prefix='promo', instance=promocao)
        formset = PromocaoQuantidadeFaixaFormSet(request.POST, prefix='faixa')
        linhas = _linhas_preenchidas(formset, ['quantidade_minima', 'valor'])
        if form.is_valid() and linhas:
            linhas = [linha for linha in linhas if linha.get('quantidade_minima') and linha.get('tipo_desconto') and linha.get('valor') is not None]
            if not linhas:
                messages.error(request, 'Informe ao menos uma faixa do combo.')
            elif _tem_duplicidade(
                linhas,
                lambda linha: (
                    linha.get('condicao_quantidade') or CondicaoQuantidade.IGUAL,
                    linha.get('quantidade_minima'),
                ),
            ):
                messages.error(request, 'Remova faixas duplicadas do combo antes de salvar.')
            else:
                with transaction.atomic():
                    promocao = form.save(commit=False)
                    promocao.filial = request.filial_ativa
                    promocao.save()
                    promocao.faixas.all().delete()
                    for linha in linhas:
                        if not linha.get('condicao_quantidade'):
                            linha['condicao_quantidade'] = CondicaoQuantidade.IGUAL
                        PromocaoQuantidadeFaixa.objects.create(promocao=promocao, **linha)
                    relatorio = _replicar_promocao_quantidade(
                        promocao,
                        _filiais_destino_request(request, 'replicar_produtos_basicos'),
                    )
                messages.success(request, 'Combo por quantidade salvo.')
                _avisar_replicacao(request, relatorio)
                return redirect(reverse('produtos:combo-promocao-list'))
        return render(request, self.template_name, self._context(request, promocao_form=form, faixa_formset=formset, active_panel='quantidade'))

    def _criar_kit_produto(self, request):
        kit = KitProduto.objects.for_filial(request.filial_ativa).filter(pk=_as_int(request.POST.get('kit_id'))).first()
        form = KitProdutoForm(request.POST, prefix='kit', instance=kit)
        formset = KitProdutoItemFormSet(request.POST, prefix='kititem', form_kwargs={'filial': request.filial_ativa})
        linhas = _linhas_preenchidas(formset, ['produto', 'quantidade'])
        if form.is_valid() and linhas:
            linhas = [linha for linha in linhas if linha.get('produto') and linha.get('quantidade')]
            if len(linhas) < 2:
                messages.error(request, 'Informe ao menos dois produtos para o kit.')
            elif _tem_duplicidade(linhas, lambda linha: linha['produto'].pk):
                messages.error(request, 'O mesmo produto foi informado mais de uma vez no kit.')
            else:
                with transaction.atomic():
                    kit = form.save(commit=False)
                    kit.filial = request.filial_ativa
                    kit.save()
                    kit.itens.all().delete()
                    for linha in linhas:
                        KitProdutoItem.objects.create(kit=kit, **linha)
                    relatorio = _replicar_kit_produto(
                        kit,
                        _filiais_destino_request(request, 'replicar_produtos_basicos'),
                    )
                messages.success(request, 'Kit de produtos salvo.')
                _avisar_replicacao(request, relatorio)
                return redirect(reverse('produtos:combo-promocao-list'))
        return render(request, self.template_name, self._context(request, kit_form=form, kit_item_formset=formset, active_panel='kit'))

    def _criar_brinde_produto(self, request):
        brinde = BrindeProduto.objects.for_filial(request.filial_ativa).filter(pk=_as_int(request.POST.get('brinde_id'))).first()
        form = BrindeProdutoForm(request.POST, filial=request.filial_ativa, prefix='brinde', instance=brinde)
        formset = BrindeProdutoItemFormSet(request.POST, prefix='brindeitem', form_kwargs={'filial': request.filial_ativa})
        linhas = _linhas_preenchidas(formset, ['produto', 'quantidade'])
        if form.is_valid() and linhas:
            linhas = [linha for linha in linhas if linha.get('produto') and linha.get('quantidade')]
            if not linhas:
                messages.error(request, 'Informe ao menos um produto para entregar como brinde.')
            elif _tem_duplicidade(linhas, lambda linha: linha['produto'].pk):
                messages.error(request, 'O mesmo produto foi informado mais de uma vez como brinde.')
            else:
                with transaction.atomic():
                    brinde = form.save(commit=False)
                    brinde.filial = request.filial_ativa
                    brinde.save()
                    brinde.itens.all().delete()
                    for linha in linhas:
                        BrindeProdutoItem.objects.create(brinde=brinde, **linha)
                    relatorio = _replicar_brinde_produto(
                        brinde,
                        _filiais_destino_request(request, 'replicar_produtos_basicos'),
                    )
                messages.success(request, 'Brinde salvo.')
                _avisar_replicacao(request, relatorio)
                return redirect(reverse('produtos:combo-promocao-list'))
        return render(request, self.template_name, self._context(request, brinde_form=form, brinde_item_formset=formset, active_panel='brinde'))

    def _criar_kit_categoria(self, request):
        kit = KitCategoria.objects.for_filial(request.filial_ativa).filter(pk=_as_int(request.POST.get('kitcat_id'))).first()
        form = KitCategoriaForm(request.POST, prefix='kitcat', instance=kit)
        formset = KitCategoriaRegraFormSet(request.POST, prefix='kitcatregra', form_kwargs={'filial': request.filial_ativa})
        linhas = _linhas_preenchidas(formset, ['categoria', 'subcategoria', 'quantidade_minima', 'valor_desconto'])
        if form.is_valid() and linhas:
            linhas = [
                linha for linha in linhas
                if linha.get('quantidade_minima') and linha.get('tipo_desconto') and linha.get('valor_desconto') is not None
            ]
            if not linhas:
                messages.error(request, 'Informe ao menos uma regra por categoria.')
            elif _tem_duplicidade(
                linhas,
                lambda linha: (
                    getattr(linha.get('categoria'), 'pk', None),
                    getattr(linha.get('subcategoria'), 'pk', None),
                ),
            ):
                messages.error(request, 'Remova regras duplicadas de categoria ou subcategoria antes de salvar.')
            else:
                with transaction.atomic():
                    kit = form.save(commit=False)
                    kit.filial = request.filial_ativa
                    kit.save()
                    kit.regras.all().delete()
                    for linha in linhas:
                        KitCategoriaRegra.objects.create(kit=kit, **linha)
                    relatorio = _replicar_kit_categoria(
                        kit,
                        _filiais_destino_request(request, 'replicar_categorias'),
                    )
                messages.success(request, 'Desconto por categoria salvo.')
                _avisar_replicacao(request, relatorio)
                return redirect(reverse('produtos:combo-promocao-list'))
        return render(request, self.template_name, self._context(request, kit_categoria_form=form, kit_categoria_regra_formset=formset, active_panel='categoria'))

    def _salvar_precos_promocionais(self, request):
        formset = PrecoPromocionalItemFormSet(
            request.POST,
            prefix='precoitem',
            form_kwargs={'filial': request.filial_ativa},
        )
        linhas = _linhas_preenchidas(formset, ['produto', 'preco_promocional', 'promocao_inicio', 'promocao_fim'])
        if linhas:
            linhas = [linha for linha in linhas if linha.get('produto') and linha.get('preco_promocional') is not None]
        if formset.is_valid() and linhas:
            if _tem_duplicidade(linhas, lambda linha: linha['produto'].pk):
                messages.error(request, 'O mesmo produto foi informado mais de uma vez em precos promocionais.')
                return render(request, self.template_name, self._context(request, preco_promocional_formset=formset, active_panel='precos'))
            replicar = request.POST.get('preco_replicar_filiais') == 'on'
            relatorio = _relatorio_replicacao()
            filiais = _filiais_destino_request(request, 'replicar_preco_venda') if replicar else Filial.objects.none()
            with transaction.atomic():
                for linha in linhas:
                    produto = linha['produto']
                    _atualizar_preco_promocional(produto, linha, request.filial_ativa, replicar_filiais=replicar)
                    if replicar:
                        for destino, filial_destino in _produtos_promocao_destino(produto, filiais, relatorio):
                            _atualizar_preco_promocional(destino, linha, filial_destino, replicar_filiais=False)
            messages.success(request, f'{len(linhas)} preco(s) promocional(is) atualizado(s).')
            _avisar_replicacao(request, relatorio)
            return redirect(reverse('produtos:combo-promocao-list'))
        messages.error(request, 'Informe ao menos um produto com preco promocional valido.')
        return render(request, self.template_name, self._context(request, preco_promocional_formset=formset, active_panel='precos'))


class ProdutoPromocaoSearchView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'

    def get(self, request):
        termo = request.GET.get('q', '').strip()
        produtos = (
            Produto.objects.for_filial(request.filial_ativa)
            .filter(ativo=True)
            .select_related('categoria__categoria_pai', 'subcategoria__categoria_pai')
        )
        if len(termo) >= 2 or termo.isdigit():
            filtro = (
                Q(descricao__icontains=termo)
                | Q(descricao_curta__icontains=termo)
                | Q(codigo__icontains=termo)
                | Q(codigo_barras__icontains=termo)
            )
            if termo.isdigit():
                filtro |= Q(pk=int(termo))
            produtos = produtos.filter(filtro)
        else:
            produtos = produtos.none()
        resultados = []
        for produto in produtos.order_by('descricao')[:20]:
            preco_base = produto.preco_venda or Decimal('0')
            preco_info = PrecoService.melhor_preco_produto_detalhado(
                produto,
                filial=request.filial_ativa,
                validar_dia_semana=False,
                minimo_dias_semana=MIN_DIAS_PROMO_AUTOMATICA,
            )
            preco_promocional = preco_info['preco']
            promocao_ativa = (
                preco_promocional < preco_base
                or PrecoService.produto_tem_promocao_vigente(
                    produto,
                    filial=request.filial_ativa,
                    validar_dia_semana=False,
                    minimo_dias_semana=MIN_DIAS_PROMO_AUTOMATICA,
                )
            )
            resultados.append({
                'id': produto.pk,
                'label': _produto_label(produto),
                'codigo_barras': produto.codigo_barras or '',
                'preco': str(produto.preco_venda or 0),
                'preco_promocional': str(preco_promocional),
                'preco_combo': str(preco_promocional if promocao_ativa else produto.preco_venda or 0),
                'promocao_origem': preco_info['origem'],
                'promocao_detalhe': preco_info['detalhe'],
                'promocao_tipo_desconto': produto.promocao_tipo_desconto,
                'promocao_valor_desconto': str(produto.promocao_valor_desconto or 0),
                'promocao_ativa': promocao_ativa,
            })
        return JsonResponse({'produtos': resultados})
