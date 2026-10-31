from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.services.exceptions import DadosInvalidosError
from apps.core.tenant_context import get_current_database_alias
from apps.financeiro.models import CondicaoPagamento, FormaPagamento
from apps.produtos.models import Produto
from apps.produtos.services.preco_service import PrecoService
from apps.vendas.models import PedidoVenda
from apps.vendas.services.venda_service import VendaService

from .models import OrcamentoWhatsApp


CENTAVOS = Decimal('0.01')


def _decimal(valor, *, padrao='0'):
    try:
        return Decimal(str(valor if valor not in (None, '') else padrao).replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DadosInvalidosError('Há um valor numérico inválido no orçamento.') from exc


def cliente_da_conversa(conversa, filial):
    queryset = Cliente.objects.for_empresa(filial.empresa).filter(ativo=True)
    cliente = None
    if conversa.cliente_operacional_id:
        cliente = queryset.filter(pk=conversa.cliente_operacional_id).first()
    if not cliente and conversa.cliente_grupo_replicacao:
        cliente = queryset.filter(
            grupo_replicacao=conversa.cliente_grupo_replicacao,
        ).order_by('pk').first()
    if not cliente and conversa.cliente_id:
        cliente = queryset.filter(pk=conversa.cliente_id).first()
    if not cliente:
        raise DadosInvalidosError(
            'Vincule esta conversa a um cliente da carteira antes de criar o orçamento.',
        )
    if cliente.bloqueado:
        raise DadosInvalidosError(f'Cliente bloqueado: {cliente.motivo_bloqueio}')
    return cliente


def endereco_cliente(cliente):
    adicional = cliente.enderecos.filter(ativo=True).order_by('-padrao', 'pk').first()
    if adicional:
        partes = [
            adicional.endereco, adicional.numero, adicional.complemento,
            adicional.bairro,
            f'{adicional.cidade}/{adicional.uf}' if adicional.cidade else adicional.uf,
            adicional.cep,
        ]
        return ', '.join(str(item) for item in partes if item)
    partes = [
        cliente.endereco, cliente.numero, cliente.complemento, cliente.bairro,
        f'{cliente.cidade}/{cliente.uf}' if cliente.cidade else cliente.uf,
        cliente.cep,
    ]
    return ', '.join(str(item) for item in partes if item)


def serializar_orcamento(orcamento):
    pedido = orcamento.pedido
    return {
        'id': orcamento.pk,
        'numero': pedido.numero_pedido,
        'status': orcamento.status,
        'status_label': orcamento.get_status_display(),
        'editavel': orcamento.status in {
            OrcamentoWhatsApp.Status.RASCUNHO,
            OrcamentoWhatsApp.Status.ENVIADO,
        },
        'forma_pagamento': pedido.forma_pagamento_id or '',
        'condicao_pagamento': pedido.condicao_pagamento_id or '',
        'validade': pedido.data_validade_orcamento.isoformat() if pedido.data_validade_orcamento else '',
        'endereco': (pedido.endereco_entrega_avulso or {}).get('descricao', ''),
        'observacao': pedido.observacao,
        'valor_frete': str(pedido.valor_frete),
        'desconto_geral_tipo': orcamento.desconto_geral_tipo,
        'desconto_geral_valor': str(orcamento.desconto_geral_valor),
        'subtotal': str(pedido.valor_produtos),
        'desconto_total': str(pedido.valor_desconto),
        'total': str(pedido.valor_total),
        'motivo_recusa': orcamento.motivo_recusa,
        'itens': [
            {
                'produto_id': item.produto_id,
                'codigo': item.produto.codigo,
                'descricao': item.produto.descricao,
                'unidade': str(item.produto.unidade_medida),
                'quantidade': str(item.quantidade),
                'valor_unitario': str(item.valor_unitario),
                'desconto_percentual': str(item.percentual_desconto),
                'valor_total': str(item.valor_total),
            }
            for item in pedido.itens.select_related('produto__unidade_medida').all()
        ],
    }


def dados_editor(conversa, filial):
    cliente = cliente_da_conversa(conversa, filial)
    orcamentos = list(
        conversa.orcamentos.select_related(
            'pedido', 'pedido__forma_pagamento', 'pedido__condicao_pagamento',
        ).prefetch_related('pedido__itens__produto__unidade_medida')[:20]
    )
    formas = FormaPagamento.objects.filter(
        empresa=filial.empresa, ativo=True,
    ).filter(Q(filial=filial) | Q(filial__isnull=True))
    condicoes = CondicaoPagamento.objects.filter(
        empresa=filial.empresa, ativo=True,
    ).order_by('descricao')
    return {
        'cliente': {
            'id': cliente.pk,
            'nome': cliente.nome_display,
            'documento': cliente.cpf_cnpj,
            'endereco': endereco_cliente(cliente),
        },
        'formas_pagamento': [
            {'id': item.pk, 'descricao': item.descricao_com_conta}
            for item in formas.order_by('descricao').distinct()
        ],
        'condicoes_pagamento': [
            {'id': item.pk, 'descricao': item.descricao}
            for item in condicoes
        ],
        'validade_padrao': (timezone.localdate() + timedelta(days=7)).isoformat(),
        'orcamentos': [serializar_orcamento(item) for item in orcamentos],
    }


def buscar_produtos(conversa, filial, termo):
    cliente = cliente_da_conversa(conversa, filial)
    termo = (termo or '').strip()
    if len(termo) < 2:
        return []
    produtos = (
        Produto.objects.for_filial(filial).vendaveis()
        .filter(Q(descricao__icontains=termo) | Q(codigo__icontains=termo))
        .select_related('unidade_medida')[:20]
    )
    retorno = []
    for produto in produtos:
        detalhe = PrecoService.preco_cliente_detalhado(
            produto, Decimal('1'), cliente=cliente,
            tabela=cliente.tabela_preco, filial=filial,
        )
        retorno.append({
            'id': produto.pk,
            'codigo': produto.codigo,
            'descricao': produto.descricao,
            'unidade': str(produto.unidade_medida),
            'preco': str(Decimal(detalhe['preco']).quantize(Decimal('0.0001'))),
            'origem_preco': detalhe.get('origem', 'Preço de venda'),
        })
    return retorno


def salvar_orcamento(*, conversa, filial, usuario, payload):
    alias = get_current_database_alias()
    with transaction.atomic(using=alias):
        cliente = cliente_da_conversa(conversa, filial)
        orcamento_id = payload.get('orcamento_id')
        orcamento = None
        if orcamento_id:
            orcamento = conversa.orcamentos.select_for_update().select_related('pedido').filter(
                pk=orcamento_id,
                status__in=[
                    OrcamentoWhatsApp.Status.RASCUNHO,
                    OrcamentoWhatsApp.Status.ENVIADO,
                ],
            ).first()
            if not orcamento:
                raise DadosInvalidosError('Este orçamento não pode mais ser editado.')
            pedido = orcamento.pedido
            pedido.status = PedidoVenda.Status.RASCUNHO
            pedido.save(update_fields=['status', 'updated_at'])
            pedido.itens.all().delete()
        else:
            pedido = VendaService.criar_pedido(
                filial=filial, usuario=usuario, cliente=cliente,
                tipo=PedidoVenda.Tipo.ORCAMENTO,
                observacao=(payload.get('observacao') or '').strip(),
            )
            pedido.origem = PedidoVenda.Origem.WHATSAPP
            orcamento = OrcamentoWhatsApp.objects.create(
                conversa=conversa, pedido=pedido,
            )

        itens = payload.get('itens') or []
        if not isinstance(itens, list) or not itens:
            raise DadosInvalidosError('Adicione ao menos um produto ao orçamento.')
        if len(itens) > 100:
            raise DadosInvalidosError('O orçamento aceita no máximo 100 produtos.')

        produtos = {
            item.pk: item
            for item in Produto.objects.for_filial(filial).vendaveis().filter(
                pk__in=[linha.get('produto_id') for linha in itens],
            )
        }
        if len(produtos) != len({str(linha.get('produto_id')) for linha in itens}):
            raise DadosInvalidosError('Um dos produtos não está disponível nesta filial.')

        for linha in itens:
            try:
                produto = produtos[int(linha.get('produto_id'))]
            except (KeyError, TypeError, ValueError) as exc:
                raise DadosInvalidosError('Um dos produtos informados é inválido.') from exc
            quantidade = _decimal(linha.get('quantidade'))
            desconto = _decimal(linha.get('desconto_percentual'))
            if quantidade <= 0 or not Decimal('0') <= desconto <= Decimal('100'):
                raise DadosInvalidosError('Revise quantidade, preço e desconto dos produtos.')
            preco = PrecoService.preco_cliente_detalhado(
                produto, quantidade, cliente=cliente,
                tabela=cliente.tabela_preco, filial=filial,
            )
            valor_unitario = Decimal(preco['preco'])
            VendaService.adicionar_item(
                pedido, produto, quantidade, valor_unitario, desconto,
            )

        forma = FormaPagamento.objects.filter(
            pk=payload.get('forma_pagamento'), empresa=filial.empresa, ativo=True,
        ).filter(Q(filial=filial) | Q(filial__isnull=True)).first() if payload.get('forma_pagamento') else None
        condicao = CondicaoPagamento.objects.filter(
            pk=payload.get('condicao_pagamento'), empresa=filial.empresa, ativo=True,
        ).first() if payload.get('condicao_pagamento') else None
        if payload.get('forma_pagamento') and not forma:
            raise DadosInvalidosError('Selecione uma forma de pagamento válida.')
        if payload.get('condicao_pagamento') and not condicao:
            raise DadosInvalidosError('Selecione uma condição de pagamento válida.')

        tipo_desconto = payload.get('desconto_geral_tipo') or OrcamentoWhatsApp.TipoDesconto.VALOR
        if tipo_desconto not in OrcamentoWhatsApp.TipoDesconto.values:
            raise DadosInvalidosError('O tipo de desconto total é inválido.')
        desconto_informado = max(_decimal(payload.get('desconto_geral_valor')), Decimal('0'))
        subtotal = sum((item.valor_bruto for item in pedido.itens.all()), Decimal('0'))
        desconto_itens = sum((item.valor_desconto for item in pedido.itens.all()), Decimal('0'))
        base_geral = max(subtotal - desconto_itens, Decimal('0'))
        if tipo_desconto == OrcamentoWhatsApp.TipoDesconto.PERCENTUAL:
            if desconto_informado > 100:
                raise DadosInvalidosError('O desconto total não pode ultrapassar 100%.')
            desconto_geral = base_geral * desconto_informado / Decimal('100')
        else:
            desconto_geral = desconto_informado
        desconto_geral = desconto_geral.quantize(CENTAVOS, rounding=ROUND_HALF_UP)
        if desconto_geral > base_geral:
            raise DadosInvalidosError('O desconto total não pode ser maior que o saldo dos produtos.')

        frete = max(_decimal(payload.get('valor_frete')), Decimal('0')).quantize(CENTAVOS)
        pedido.tipo = PedidoVenda.Tipo.ORCAMENTO
        pedido.status = PedidoVenda.Status.RASCUNHO
        pedido.origem = PedidoVenda.Origem.WHATSAPP
        pedido.forma_pagamento = forma
        pedido.condicao_pagamento = condicao
        pedido.prazo_pagamento_dias = condicao.dias_primeira_parcela if condicao else 0
        validade = payload.get('validade')
        try:
            pedido.data_validade_orcamento = date.fromisoformat(validade) if validade else None
        except (TypeError, ValueError) as exc:
            raise DadosInvalidosError('A data de validade do orçamento é inválida.') from exc
        pedido.endereco_entrega = None
        pedido.endereco_entrega_avulso = {
            'descricao': (payload.get('endereco') or endereco_cliente(cliente)).strip(),
        }
        pedido.observacao = (payload.get('observacao') or '').strip()[:5000]
        pedido.valor_frete = frete
        pedido.valor_produtos = subtotal.quantize(CENTAVOS)
        pedido.valor_desconto = (desconto_itens + desconto_geral).quantize(CENTAVOS)
        pedido.desconto_valor = pedido.valor_desconto
        pedido.desconto_percentual = Decimal('0')
        pedido.valor_total = max(
            pedido.valor_produtos + frete - pedido.valor_desconto,
            Decimal('0'),
        ).quantize(CENTAVOS)
        pedido.save()

        orcamento.status = OrcamentoWhatsApp.Status.RASCUNHO
        orcamento.desconto_geral_tipo = tipo_desconto
        orcamento.desconto_geral_valor = desconto_informado
        orcamento.enviado_em = None
        orcamento.respondido_em = None
        orcamento.motivo_recusa = ''
        orcamento.save()
        return orcamento


def resumo_para_whatsapp(orcamento, link):
    pedido = orcamento.pedido
    linhas = [
        f'*Orçamento {pedido.numero_pedido}*',
        f'*Cliente:* {pedido.cliente.nome_display}',
        '',
    ]
    for item in pedido.itens.select_related('produto').all():
        linhas.append(
            f'{item.quantidade:g}x {item.produto.descricao} — '
            f'R$ {item.valor_unitario:.2f} — total R$ {item.valor_total:.2f}'
        )
        if item.percentual_desconto:
            linhas.append(f'  Desconto do item: {item.percentual_desconto:.2f}%')
    linhas.extend([
        '',
        f'*Subtotal:* R$ {pedido.valor_produtos:.2f}',
        f'*Descontos:* R$ {pedido.valor_desconto:.2f}',
        f'*Frete:* R$ {pedido.valor_frete:.2f}',
        f'*Total:* R$ {pedido.valor_total:.2f}',
        f'*Pagamento:* {pedido.forma_pagamento or "A combinar"}' + (
            f' · {pedido.condicao_pagamento}' if pedido.condicao_pagamento else ''
        ),
        f'*Entrega:* {(pedido.endereco_entrega_avulso or {}).get("descricao") or "A combinar"}',
        f'*Validade:* {pedido.data_validade_orcamento:%d/%m/%Y}' if pedido.data_validade_orcamento else '',
        '',
        'Confira todos os detalhes e aprove ou informe o motivo da recusa:',
        link,
    ])
    return '\n'.join(linha for linha in linhas if linha is not None)
