import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views import View

from apps.core.models.parametros import ParametrosSistema
from apps.core.tenant_context import get_current_database_alias, tenant_atomic
from apps.pdv.models import VendaPDV
from apps.produtos.models import Produto
from apps.whatsapp_agent.tracking import conversa_do_token

from .models import (
    CatalogoConfiguracao, CatalogoLinkPublico, CupomCatalogo,
    ItemPedidoCatalogo, PedidoCatalogo,
)
from .services import enviar_resumo_whatsapp, localizar_cliente, numero_whatsapp, obter_ou_criar_cliente


def _arquivo_url(campo):
    if not campo:
        return ''
    try:
        return campo.url
    except (ValueError, AttributeError):
        return ''


def _logo(filial):
    if _arquivo_url(filial.imagem):
        return _arquivo_url(filial.imagem)
    parametros = ParametrosSistema.objects.filter(filial=filial).first()
    if parametros:
        return _arquivo_url(parametros.logo) or parametros.logo_url
    return filial.empresa.logo_url or ''


def _produtos(filial):
    return list(
        Produto.objects.for_filial(filial).filter(
            ativo=True, exibir_catalogo=True,
        ).exclude(tipo_produto=Produto.TipoProduto.SERVICO).select_related(
            'categoria', 'subcategoria',
        ).order_by('-catalogo_destaque', 'catalogo_ordem', 'descricao')
    )


def _endereco_loja(filial):
    primeira_linha = ', '.join(item for item in (filial.endereco, filial.numero) if item)
    segunda_linha = ' · '.join(item for item in (
        filial.bairro,
        f'{filial.cidade}/{filial.uf}' if filial.cidade and filial.uf else filial.cidade or filial.uf,
    ) if item)
    return ' — '.join(item for item in (primeira_linha, segunda_linha) if item)


def _resumo_dias(dias):
    nomes = ('Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom')
    selecionados = sorted({int(dia) for dia in (dias or []) if str(dia).isdigit() and 0 <= int(dia) <= 6})
    if selecionados == list(range(7)):
        return 'Todos os dias'
    if selecionados == list(range(5)):
        return 'Seg a sex'
    if selecionados == list(range(6)):
        return 'Seg a sáb'
    return ', '.join(nomes[dia] for dia in selecionados) or 'Sem dias configurados'


def _funcionamento(config, agora=None):
    agora = timezone.localtime(agora or timezone.now())
    dias = {int(dia) for dia in (config.dias_funcionamento or [])}
    abertura, fechamento = config.horario_abertura, config.horario_fechamento
    horario = agora.time().replace(tzinfo=None)
    dia = agora.weekday()
    if abertura < fechamento:
        aberto = dia in dias and abertura <= horario < fechamento
    else:
        aberto = (dia in dias and horario >= abertura) or (
            (dia - 1) % 7 in dias and horario < fechamento
        )
    return {
        'aberto': aberto,
        'status': f'Aberto agora · até {fechamento:%H:%M}' if aberto else 'Fechado agora',
        'horarios': f'{_resumo_dias(dias)} · {abertura:%H:%M} às {fechamento:%H:%M}',
    }


def _cupom_valido(filial, codigo, subtotal):
    codigo = (codigo or '').strip().upper()
    if not codigo:
        return None, ''
    cupom = CupomCatalogo.objects.filter(filial=filial, codigo__iexact=codigo).first()
    if not cupom:
        return None, 'Cupom não encontrado.'
    motivo = cupom.motivo_indisponivel(subtotal)
    return (None, motivo) if motivo else (cupom, '')


def _historico_compras(cliente, filial):
    if not cliente:
        return []
    registros = []
    vendas_pdv = (
        VendaPDV.objects.for_filial(filial).filter(
            cliente=cliente, status='finalizada', bonificacao=False,
        ).prefetch_related('itens__produto').order_by('-data_venda')[:6]
    )
    for venda in vendas_pdv:
        registros.append({
            'momento': venda.data_venda,
            'numero': f'Venda #{venda.numero_venda:06d}',
            'total': venda.valor_total,
            'itens': [
                {
                    'id': item.produto_id,
                    'nome': item.produto.descricao,
                    'quantidade': float(item.quantidade),
                }
                for item in venda.itens.all()
                if item.quantidade > 0
            ],
        })

    # Pedidos antigos do catálogo que ainda não possuem uma venda PDV
    # vinculada também são compras válidas. Os que possuem vínculo já estão
    # representados por VendaPDV e não podem aparecer duas vezes.
    pedidos_catalogo = (
        PedidoCatalogo.objects.filter(
            filial=filial, cliente=cliente, venda_pdv__isnull=True,
            status__in=[
                PedidoCatalogo.Status.PAGO,
                PedidoCatalogo.Status.SAIU_ENTREGA,
                PedidoCatalogo.Status.ENTREGUE,
            ],
        ).prefetch_related('itens').order_by('-created_at')[:6]
    )
    for pedido in pedidos_catalogo:
        registros.append({
            'momento': pedido.created_at,
            'numero': pedido.numero,
            'total': pedido.total,
            'itens': [
                {
                    'id': item.produto_id,
                    'nome': item.descricao,
                    'quantidade': item.quantidade,
                }
                for item in pedido.itens.all()
            ],
        })

    registros.sort(key=lambda item: item['momento'], reverse=True)
    return [
        {
            'numero': item['numero'],
            'data': timezone.localtime(item['momento']).strftime('%d/%m/%Y'),
            'total': float(item['total']),
            'itens': item['itens'],
        }
        for item in registros[:3]
    ]


def _contexto(link, *, dados=None, erros=None, pedido=None, aviso=''):
    config, _ = CatalogoConfiguracao.objects.get_or_create(filial=link.filial)
    produtos = _produtos(link.filial)
    grupos_categoria = {}
    catalogo_json = []
    for produto in produtos:
        categoria = produto.categoria.nome if produto.categoria else 'Outros'
        chave_categoria = f'categoria-{produto.categoria_id}' if produto.categoria_id else 'outros'
        grupos_categoria.setdefault(chave_categoria, {'chave': chave_categoria, 'nome': categoria, 'produtos': []})[
            'produtos'
        ].append(produto)
        catalogo_json.append({
            'id': produto.pk, 'nome': produto.descricao,
            'preco': float(produto.preco_venda),
            'maximo': produto.catalogo_quantidade_maxima,
            'descricao': produto.catalogo_descricao or produto.descricao_curta,
            'destaque': produto.catalogo_destaque,
            'categoria': chave_categoria,
        })
    destaques = [produto for produto in produtos if produto.catalogo_destaque]
    grupos = []
    if destaques:
        grupos.append({'chave': 'destaques', 'nome': 'Destaques', 'produtos': destaques})
    grupos.extend(grupos_categoria.values())
    funcionamento = _funcionamento(config)
    return {
        'link': link, 'filial': link.filial, 'configuracao': config,
        'logo_url': _logo(link.filial), 'grupos': grupos,
        'endereco_loja': _endereco_loja(link.filial),
        'funcionamento': funcionamento,
        'catalogo_json': catalogo_json, 'dados': dados or {}, 'erros': erros or [],
        'pedido': pedido, 'aviso': aviso,
        'historico_json': [],
        'data_minima': timezone.localdate().isoformat(),
        'data_maxima': (timezone.localdate() + timedelta(days=90)).isoformat(),
    }


class _CatalogoPublicoBase(View):
    template_name = 'catalogo/publico/catalogo.html'

    def _link(self, token):
        return get_object_or_404(
            CatalogoLinkPublico.objects.select_related('filial', 'filial__empresa'),
            token=token, ativo=True, filial__ativo=True,
        )

    def get(self, request, token):
        link = self._link(token)
        conversa = conversa_do_token((request.GET.get('wa') or '').strip())
        dados = {}
        ultimos = []
        if conversa:
            dados = {'nome': conversa.nome_contato, 'telefone': conversa.telefone}
            cliente = localizar_cliente(link.filial, conversa.telefone)
            if cliente:
                dados['nome'] = cliente.nome_display
                dados.update({
                    'logradouro': cliente.endereco, 'numero': cliente.numero,
                    'complemento': cliente.complemento, 'bairro': cliente.bairro,
                    'cidade': cliente.cidade, 'uf': cliente.uf, 'cep': cliente.cep,
                })
                ultimos = _historico_compras(cliente, link.filial)
        contexto = _contexto(link, dados=dados)
        contexto.update({
            'rastreamento_whatsapp': request.GET.get('wa', ''),
            'ultimos_pedidos': ultimos,
            'historico_json': ultimos,
        })
        return render(request, self.template_name, contexto)


class CatalogoPublicoView(_CatalogoPublicoBase):
    def post(self, request, token):
        link = self._link(token)
        config, _ = CatalogoConfiguracao.objects.get_or_create(filial=link.filial)
        dados = {chave: (request.POST.get(chave) or '').strip() for chave in (
            'nome', 'telefone', 'modalidade', 'forma_pagamento', 'troco_para',
            'logradouro', 'numero', 'complemento', 'bairro', 'cidade', 'uf', 'cep',
            'data_entrega', 'hora_entrega', 'observacao', 'cupom_codigo',
        )}
        erros = []
        if not config.ativo:
            erros.append('O catálogo está temporariamente indisponível.')
        if len(dados['nome']) < 2:
            erros.append('Informe seu nome.')
        telefone = numero_whatsapp(dados['telefone'])
        if not telefone:
            erros.append('Informe um WhatsApp válido com DDD.')
        if dados['modalidade'] not in PedidoCatalogo.Modalidade.values:
            erros.append('Escolha entrega ou retirada.')
        if dados['modalidade'] == PedidoCatalogo.Modalidade.ENTREGA and not config.entrega_ativa:
            erros.append('A entrega não está disponível.')
        if dados['modalidade'] == PedidoCatalogo.Modalidade.RETIRADA and not config.retirada_ativa:
            erros.append('A retirada não está disponível.')
        if dados['forma_pagamento'] not in PedidoCatalogo.Pagamento.values:
            erros.append('Escolha a forma de pagamento.')
        try:
            carrinho = json.loads(request.POST.get('carrinho_json') or '[]')
        except json.JSONDecodeError:
            carrinho = []
        if not isinstance(carrinho, list) or not carrinho or len(carrinho) > 50:
            erros.append('Adicione ao menos um produto ao carrinho.')
            carrinho = []
        quantidades = {}
        for linha in carrinho:
            try:
                produto_id = int(linha.get('id'))
                quantidade = int(linha.get('quantidade'))
            except (TypeError, ValueError, AttributeError):
                continue
            if quantidade > 0:
                quantidades[produto_id] = min(quantidade, 999)
        produtos = {
            item.pk: item for item in Produto.objects.for_filial(link.filial).filter(
                pk__in=quantidades, ativo=True, exibir_catalogo=True,
            ).exclude(tipo_produto=Produto.TipoProduto.SERVICO)
        }
        if len(produtos) != len(quantidades):
            erros.append('Um produto do carrinho não está mais disponível.')
        for produto_id, quantidade in quantidades.items():
            produto = produtos.get(produto_id)
            if produto and quantidade > produto.catalogo_quantidade_maxima:
                erros.append(
                    f'A quantidade máxima de {produto.descricao} é '
                    f'{produto.catalogo_quantidade_maxima}.'
                )
        subtotal = sum(
            (Decimal(produtos[pk].preco_venda) * quantidade for pk, quantidade in quantidades.items() if pk in produtos),
            Decimal('0'),
        )
        if subtotal < config.pedido_minimo:
            erros.append(f'O pedido mínimo é R$ {config.pedido_minimo:.2f}.'.replace('.', ','))
        cupom, erro_cupom = _cupom_valido(link.filial, dados['cupom_codigo'], subtotal)
        if erro_cupom:
            erros.append(erro_cupom)
        valor_desconto = cupom.calcular_desconto(subtotal) if cupom else Decimal('0')
        endereco = {chave: dados[chave] for chave in ('logradouro', 'numero', 'complemento', 'bairro', 'cidade', 'uf', 'cep')}
        if dados['modalidade'] == PedidoCatalogo.Modalidade.ENTREGA:
            if not dados['logradouro'] or not dados['numero'] or not dados['bairro'] or not dados['cidade'] or len(dados['uf']) != 2:
                erros.append('Preencha o endereço completo para entrega.')
        entrega_em = None
        if config.agendamento_entrega_ativo and dados['data_entrega'] and dados['hora_entrega']:
            try:
                entrega_em = timezone.make_aware(
                    datetime.strptime(f"{dados['data_entrega']} {dados['hora_entrega']}", '%Y-%m-%d %H:%M'),
                    timezone.get_current_timezone(),
                )
                if entrega_em < timezone.now() + timedelta(hours=config.prazo_minimo_entrega_horas):
                    raise ValueError
            except ValueError:
                erros.append('Escolha uma data e um horário de entrega válidos.')
        troco = None
        if dados['forma_pagamento'] == PedidoCatalogo.Pagamento.DINHEIRO:
            try:
                troco = Decimal(dados['troco_para'].replace(',', '.')) if dados['troco_para'] else None
            except InvalidOperation:
                erros.append('Informe um valor válido para o troco.')
        valor_frete, frete_a_combinar = config.calcular_frete(subtotal, dados['modalidade'])
        valor_frete = Decimal(valor_frete)
        if erros:
            contexto = _contexto(link, dados=dados, erros=erros)
            contexto['rastreamento_whatsapp'] = request.POST.get('origem_whatsapp', '')
            return render(request, self.template_name, contexto, status=400)
        rastreamento_whatsapp = (request.POST.get('origem_whatsapp') or '').strip()
        conversa = conversa_do_token(rastreamento_whatsapp)
        observacao = dados['observacao'][:2000]
        if conversa:
            observacao = 'Pedido feito pelo WhatsApp.' + (
                f' {observacao}' if observacao else ''
            )
        with tenant_atomic():
            cliente = obter_ou_criar_cliente(link.filial, dados['nome'][:150], telefone, endereco)
            pedido = PedidoCatalogo.objects.create(
                filial=link.filial, numero=f'CAT-{timezone.now():%y%m%d%H%M%S}', cliente=cliente,
                nome_cliente=dados['nome'][:150], telefone=telefone,
                modalidade=dados['modalidade'], forma_pagamento=dados['forma_pagamento'],
                troco_para=troco, endereco_entrega=endereco if dados['modalidade'] == 'entrega' else {},
                entrega_em=entrega_em, observacao=observacao,
                subtotal=subtotal, cupom=cupom,
                codigo_cupom=cupom.codigo if cupom else '', valor_desconto=valor_desconto,
                valor_frete=valor_frete, frete_a_combinar=frete_a_combinar,
                total=max(Decimal('0'), subtotal - valor_desconto) + valor_frete,
            )
            for produto_id, quantidade in quantidades.items():
                produto = produtos[produto_id]
                ItemPedidoCatalogo.objects.create(
                    pedido=pedido, produto=produto, descricao=produto.descricao,
                    quantidade=quantidade, valor_unitario=produto.preco_venda,
                    valor_total=Decimal(produto.preco_venda) * quantidade,
                )
        _ok, aviso = enviar_resumo_whatsapp(
            pedido, db_alias=get_current_database_alias(), conversa=conversa,
        )
        contexto = _contexto(link, pedido=pedido, aviso=aviso)
        contexto['rastreamento_whatsapp'] = request.POST.get('origem_whatsapp', '')
        return render(request, self.template_name, contexto)


class ClienteCatalogoView(View):
    def get(self, request, token):
        link = get_object_or_404(
            CatalogoLinkPublico.objects.select_related('filial'),
            token=token, ativo=True, filial__ativo=True,
        )
        cliente = localizar_cliente(link.filial, request.GET.get('telefone', ''))
        dados = {'encontrado': bool(cliente)}
        if cliente:
            ultimos = _historico_compras(cliente, link.filial)
            dados.update({
                'nome': cliente.nome_display, 'logradouro': cliente.endereco,
                'numero': cliente.numero, 'complemento': cliente.complemento,
                'bairro': cliente.bairro, 'cidade': cliente.cidade,
                'uf': cliente.uf, 'cep': cliente.cep,
                'historico': ultimos,
            })
        resposta = JsonResponse(dados)
        resposta['Cache-Control'] = 'no-store'
        return resposta


class CupomCatalogoPublicoView(View):
    def post(self, request, token):
        link = get_object_or_404(
            CatalogoLinkPublico.objects.select_related('filial'),
            token=token, ativo=True, filial__ativo=True,
        )
        try:
            payload = json.loads(request.body or b'{}')
            subtotal = Decimal(str(payload.get('subtotal') or '0'))
        except (json.JSONDecodeError, InvalidOperation, TypeError, ValueError):
            return JsonResponse({'erro': 'Não foi possível validar o cupom.'}, status=400)
        cupom, erro = _cupom_valido(link.filial, payload.get('codigo'), subtotal)
        if erro:
            return JsonResponse({'erro': erro}, status=400)
        if not cupom:
            return JsonResponse({'erro': 'Informe o código do cupom.'}, status=400)
        return JsonResponse({
            'codigo': cupom.codigo,
            'tipo': cupom.tipo,
            'valor': float(cupom.valor),
            'pedido_minimo': float(cupom.pedido_minimo),
            'desconto': float(cupom.calcular_desconto(subtotal)),
        })
