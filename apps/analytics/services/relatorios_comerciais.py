"""Relatórios comerciais inspirados nas consultas de faturamento do TOTVS."""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum

from apps.financeiro.services.receita import ajuste_por_cliente, ajuste_por_grupo
from apps.pdv.models import DevolucaoPDV, ItemVendaPDV, VendaPDV
from apps.vendas.models import DevolucaoVenda, ItemPedidoVenda, PedidoVenda

ZERO = Decimal('0')
STATUS_PEDIDO_REALIZADO = (
    PedidoVenda.Status.FATURADO, PedidoVenda.Status.PARCIALMENTE_FATURADO,
    PedidoVenda.Status.EM_ENTREGA, PedidoVenda.Status.ENTREGUE,
)


def _percentual(valor, base):
    return (valor / base * 100).quantize(Decimal('0.1')) if base else ZERO


def _somar_linhas(destino, linhas, chave, rotulo):
    for linha in linhas:
        identificador = linha[chave]
        item = destino.setdefault(identificador, {
            'id': identificador, 'nome': linha.get(rotulo) or 'Não identificado',
            'quantidade': ZERO, 'faturamento': ZERO, 'descontos': ZERO,
            'custo': ZERO, 'vendas': 0,
        })
        for campo in ('quantidade', 'faturamento', 'descontos', 'custo'):
            item[campo] += linha.get(campo) or ZERO
        item['vendas'] += linha.get('vendas') or 0


def _custo_total():
    """Cria uma expressão nova e evita colisão com a anotação `quantidade`."""
    return Sum(
        F('custo_unitario_snapshot') * F('quantidade'),
        output_field=DecimalField(max_digits=20, decimal_places=4),
    )


def _descontar_ajustes(valores, ajustes):
    for identificador, ajuste in ajustes.items():
        if identificador in valores:
            valores[identificador]['faturamento'] = max(
                ZERO, valores[identificador]['faturamento'] - ajuste,
            )


def _ajuste_produtos_pdv(vendas_qs):
    """Rateia desconto geral, acréscimo e pagamentos sem receita nos itens."""
    nao_contabilizado = {
        chave[0]: valor
        for chave, valor in ajuste_por_grupo(vendas_qs, 'venda_pdv_id').items()
    }
    ajustes_venda = {}
    for venda in vendas_qs.values('id', 'valor_subtotal', 'valor_total'):
        subtotal = venda['valor_subtotal'] or ZERO
        realizado = (venda['valor_total'] or ZERO) - nao_contabilizado.get(venda['id'], ZERO)
        ajuste = subtotal - max(ZERO, realizado)
        if ajuste and subtotal:
            ajustes_venda[venda['id']] = (ajuste, subtotal)
    if not ajustes_venda:
        return {}

    ajustes = {}
    itens = (
        ItemVendaPDV.objects.filter(venda_pdv_id__in=ajustes_venda)
        .values('venda_pdv_id', 'produto_id').annotate(valor=Sum('valor_total'))
    )
    for item in itens:
        ajuste, subtotal = ajustes_venda[item['venda_pdv_id']]
        parcela = ajuste * ((item['valor'] or ZERO) / subtotal)
        produto_id = item['produto_id']
        ajustes[produto_id] = ajustes.get(produto_id, ZERO) + parcela
    return ajustes


class RentabilidadeService:
    @classmethod
    def apurar(cls, filial, inicio, fim):
        vendas_pdv = VendaPDV.objects.filter(
            filial=filial, status='finalizada', bonificacao=False,
            data_venda__date__range=(inicio, fim),
        )
        itens_pdv = ItemVendaPDV.objects.filter(venda_pdv__in=vendas_pdv)
        itens_pedido = ItemPedidoVenda.objects.filter(
            pedido__filial=filial, pedido__status__in=STATUS_PEDIDO_REALIZADO,
            pedido__data_emissao__date__range=(inicio, fim),
        )
        produtos = {}
        _somar_linhas(produtos, itens_pdv.values(
            'produto_id', 'produto__descricao',
        ).annotate(
            custo=_custo_total(), quantidade=Sum('quantidade'),
            faturamento=Sum('valor_total'), descontos=Sum('desconto_valor'),
            vendas=Count('venda_pdv_id', distinct=True),
        ), 'produto_id', 'produto__descricao')
        _descontar_ajustes(produtos, _ajuste_produtos_pdv(vendas_pdv))
        _somar_linhas(produtos, itens_pedido.values(
            'produto_id', 'produto__descricao',
        ).annotate(
            custo=_custo_total(), quantidade=Sum('quantidade'),
            faturamento=Sum('valor_total'), descontos=Sum('valor_desconto'),
            vendas=Count('pedido_id', distinct=True),
        ), 'produto_id', 'produto__descricao')

        clientes = {}
        _somar_linhas(clientes, itens_pdv.values(
            'venda_pdv__cliente_id', 'venda_pdv__cliente__razao_social',
        ).annotate(
            custo=_custo_total(), quantidade=Sum('quantidade'),
            faturamento=Sum('valor_total'), descontos=Sum('desconto_valor'),
            vendas=Count('venda_pdv_id', distinct=True),
        ), 'venda_pdv__cliente_id', 'venda_pdv__cliente__razao_social')
        receitas_pdv = {
            linha['cliente_id']: linha['faturamento'] or ZERO
            for linha in vendas_pdv.values('cliente_id').annotate(faturamento=Sum('valor_total'))
        }
        ajustes_cliente = ajuste_por_cliente(vendas_pdv)
        for cliente_id, faturamento in receitas_pdv.items():
            clientes[cliente_id]['faturamento'] = max(
                ZERO, faturamento - ajustes_cliente.get(cliente_id, ZERO),
            )
        _somar_linhas(clientes, itens_pedido.values(
            'pedido__cliente_id', 'pedido__cliente__razao_social',
        ).annotate(
            custo=_custo_total(), quantidade=Sum('quantidade'),
            faturamento=Sum('valor_total'), descontos=Sum('valor_desconto'),
            vendas=Count('pedido_id', distinct=True),
        ), 'pedido__cliente_id', 'pedido__cliente__razao_social')

        def finalizar(valores):
            linhas = list(valores.values())
            for linha in linhas:
                linha['margem'] = linha['faturamento'] - linha['custo']
                linha['margem_pct'] = _percentual(linha['margem'], linha['faturamento'])
            return sorted(linhas, key=lambda item: (item['margem'], item['faturamento']), reverse=True)

        produtos = finalizar(produtos)
        clientes = finalizar(clientes)
        resumo = {
            'faturamento': sum((item['faturamento'] for item in produtos), ZERO),
            'custo': sum((item['custo'] for item in produtos), ZERO),
            'descontos': sum((item['descontos'] for item in produtos), ZERO),
        }
        resumo['margem'] = resumo['faturamento'] - resumo['custo']
        resumo['margem_pct'] = _percentual(resumo['margem'], resumo['faturamento'])
        return {'produtos': produtos, 'clientes': clientes, 'resumo': resumo}


class OcorrenciasComerciaisService:
    @staticmethod
    def apurar(filial, inicio, fim):
        cancelamentos = []
        for venda in VendaPDV.objects.filter(
            filial=filial, status='cancelada', cancelado_em__date__range=(inicio, fim),
        ).select_related('cliente', 'cancelado_por'):
            cancelamentos.append({
                'data': venda.cancelado_em, 'origem': 'PDV', 'documento': venda.numero_venda,
                'cliente': str(venda.cliente) if venda.cliente_id else 'Consumidor',
                'valor': venda.valor_total, 'motivo': venda.motivo_cancelamento or 'Não informado',
                'usuario': venda.cancelado_por.nome if venda.cancelado_por_id else 'Não identificado',
            })
        for pedido in PedidoVenda.objects.filter(
            filial=filial, status=PedidoVenda.Status.CANCELADO,
            updated_at__date__range=(inicio, fim),
        ).select_related('cliente', 'usuario'):
            cancelamentos.append({
                'data': pedido.updated_at, 'origem': 'Pedido', 'documento': pedido.numero_pedido,
                'cliente': str(pedido.cliente), 'valor': pedido.valor_total,
                'motivo': pedido.motivo_cancelamento or 'Não informado',
                'usuario': pedido.usuario.nome if pedido.usuario_id else 'Não identificado',
            })

        devolucoes = []
        for dev in DevolucaoPDV.objects.filter(
            filial=filial, created_at__date__range=(inicio, fim),
        ).select_related('venda_pdv__cliente', 'usuario'):
            devolucoes.append({
                'data': dev.created_at, 'origem': 'PDV', 'documento': dev.venda_pdv.numero_venda,
                'cliente': str(dev.venda_pdv.cliente) if dev.venda_pdv.cliente_id else 'Consumidor',
                'valor': dev.valor_estorno, 'motivo': dev.motivo,
                'usuario': dev.usuario.nome if dev.usuario_id else 'Não identificado',
            })
        for dev in DevolucaoVenda.objects.filter(
            filial=filial, data_devolucao__range=(inicio, fim),
        ).select_related('pedido__cliente', 'usuario'):
            devolucoes.append({
                'data': dev.data_devolucao, 'origem': 'Pedido', 'documento': dev.numero,
                'cliente': str(dev.pedido.cliente), 'valor': dev.valor_total,
                'motivo': dev.get_motivo_display(),
                'usuario': dev.usuario.nome if dev.usuario_id else 'Não identificado',
            })

        descontos = []
        for venda in VendaPDV.objects.filter(
            filial=filial, status='finalizada', data_venda__date__range=(inicio, fim),
        ).annotate(
            desconto_itens=Sum('itens__desconto_valor'),
        ).filter(
            Q(valor_desconto__gt=0) | Q(desconto_itens__gt=0),
        ).select_related('cliente', 'usuario'):
            desconto_total = (venda.valor_desconto or ZERO) + (venda.desconto_itens or ZERO)
            descontos.append({
                'data': venda.data_venda, 'origem': 'PDV', 'documento': venda.numero_venda,
                'cliente': str(venda.cliente) if venda.cliente_id else 'Consumidor',
                'valor': desconto_total, 'base': venda.valor_subtotal + desconto_total,
                'usuario': venda.usuario.nome if venda.usuario_id else 'Não identificado',
            })
        for pedido in PedidoVenda.objects.filter(
            filial=filial, status__in=STATUS_PEDIDO_REALIZADO,
            data_emissao__date__range=(inicio, fim), valor_desconto__gt=0,
        ).select_related('cliente', 'usuario'):
            descontos.append({
                'data': pedido.data_emissao, 'origem': 'Pedido', 'documento': pedido.numero_pedido,
                'cliente': str(pedido.cliente), 'valor': pedido.valor_desconto,
                'base': pedido.valor_produtos, 'usuario': pedido.usuario.nome,
            })
        return {
            'cancelamentos': sorted(cancelamentos, key=lambda item: item['data'], reverse=True),
            'devolucoes': sorted(devolucoes, key=lambda item: item['data'], reverse=True),
            'descontos': sorted(descontos, key=lambda item: item['data'], reverse=True),
            'totais': {
                'cancelamentos': sum((item['valor'] for item in cancelamentos), ZERO),
                'devolucoes': sum((item['valor'] for item in devolucoes), ZERO),
                'descontos': sum((item['valor'] for item in descontos), ZERO),
            },
        }
