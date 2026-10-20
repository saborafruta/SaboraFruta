"""Experiência operacional da força de vendas externa."""

import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views import View

from apps.cadastros.models import Cliente
from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.tenant_context import tenant_atomic
from apps.estoque.models import Deposito
from apps.estoque.services.saldos_por_deposito import anexar_saldos_por_deposito
from apps.financeiro.models import CondicaoPagamento, FormaPagamento
from apps.pdv.services.produto_vendavel_service import ProdutoVendavelService
from apps.produtos.models import CategoriaProduto, Produto
from apps.produtos.services.imagem_produto_service import foto_produto_thumbnail_url
from apps.vendas.models import PedidoVenda
from apps.vendas.services.dashboard_forca_vendas import montar_dashboard
from apps.vendas.services.venda_service import VendaService


def _pode_ver_equipe(request):
    perfil = request.user.perfil
    return bool(
        request.user.is_superuser
        or perfil.is_admin
        or request.user.tem_permissao('vendas', 'aprovar')
        or request.user.tem_permissao('vendas', 'editar')
    )


def _pedidos_visiveis(request):
    qs = (
        PedidoVenda.objects.for_filial(request.filial_ativa)
        .filter(origem=PedidoVenda.Origem.EXTERNO)
        .select_related('cliente', 'usuario', 'representante', 'tabela_preco')
        .prefetch_related('itens__produto')
    )
    return qs if _pode_ver_equipe(request) else qs.filter(usuario=request.user)


class DashboardForcaVendasView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    template_name = 'vendas/forca_vendas/dashboard.html'

    def get(self, request):
        contexto = montar_dashboard(
            pedidos_visiveis=_pedidos_visiveis(request),
            filial=request.filial_ativa,
            params=request.GET,
        )
        contexto.update({
            'title': 'Dashboard da força de vendas',
            'visao_equipe': _pode_ver_equipe(request),
        })
        return render(request, self.template_name, contexto)


class PedidoExternoNovoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'criar'
    template_name = 'vendas/forca_vendas/novo_pedido.html'

    def get(self, request):
        formas = FormaPagamento.objects.filter(
            empresa=request.filial_ativa.empresa,
            ativo=True,
        ).filter(Q(filial=request.filial_ativa) | Q(filial__isnull=True)).order_by('descricao')
        condicoes = CondicaoPagamento.objects.filter(
            empresa=request.filial_ativa.empresa,
            ativo=True,
        ).order_by('descricao')
        return render(request, self.template_name, {
            'title': 'Novo pedido externo',
            'categorias': CategoriaProduto.objects.for_filial(request.filial_ativa)
            .filter(ativo=True).order_by('nome'),
            'formas_pagamento': formas,
            'condicoes_pagamento': condicoes,
        })

    def post(self, request):
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'erro': 'Dados do pedido inválidos.'}, status=400)
        if not isinstance(body, dict):
            return JsonResponse({'erro': 'Dados do pedido inválidos.'}, status=400)

        itens = body.get('itens') or []
        if not isinstance(itens, list) or not itens:
            return JsonResponse({'erro': 'Adicione ao menos um produto.'}, status=400)

        cliente = (
            Cliente.objects.for_filial(request.filial_ativa)
            .filter(pk=body.get('cliente_id'), ativo=True)
            .select_related('tabela_preco')
            .first()
        )
        if not cliente:
            return JsonResponse({'erro': 'Selecione um cliente válido.'}, status=400)

        prioridade = body.get('prioridade') or PedidoVenda.Prioridade.NORMAL
        prioridades_validas = {valor for valor, _ in PedidoVenda.Prioridade.choices}
        if prioridade not in prioridades_validas:
            prioridade = PedidoVenda.Prioridade.NORMAL

        data_entrega = parse_date(body.get('data_entrega_prevista') or '')
        forma_pagamento = FormaPagamento.objects.filter(
            pk=body.get('forma_pagamento_id'),
            empresa=request.filial_ativa.empresa,
            ativo=True,
        ).filter(Q(filial=request.filial_ativa) | Q(filial__isnull=True)).first()
        if not forma_pagamento:
            return JsonResponse({'erro': 'Selecione uma forma de pagamento válida.'}, status=400)
        condicao_pagamento = CondicaoPagamento.objects.filter(
            pk=body.get('condicao_pagamento_id'),
            empresa=request.filial_ativa.empresa,
            ativo=True,
        ).first() if body.get('condicao_pagamento_id') else None
        try:
            prazo_pagamento = max(int(body.get('prazo_pagamento_dias') or 0), 0)
        except (TypeError, ValueError):
            return JsonResponse({'erro': 'O prazo de pagamento é inválido.'}, status=400)

        try:
            with tenant_atomic():
                pedido = VendaService.criar_pedido(
                    filial=request.filial_ativa,
                    usuario=request.user,
                    cliente=cliente,
                    tipo=PedidoVenda.Tipo.PEDIDO,
                    observacao=(body.get('observacao') or '').strip(),
                )
                pedido.origem = PedidoVenda.Origem.EXTERNO
                pedido.prioridade = prioridade
                pedido.data_entrega_prevista = data_entrega
                pedido.observacao_interna = (body.get('observacao_interna') or '').strip()
                pedido.forma_pagamento = forma_pagamento
                pedido.condicao_pagamento = condicao_pagamento
                pedido.prazo_pagamento_dias = prazo_pagamento
                pedido.save(update_fields=[
                    'origem', 'prioridade', 'data_entrega_prevista',
                    'observacao_interna', 'forma_pagamento', 'condicao_pagamento',
                    'prazo_pagamento_dias', 'updated_at',
                ])

                for item in itens:
                    if not isinstance(item, dict):
                        raise ValueError('Item do pedido inválido.')
                    try:
                        quantidade = Decimal(str(item.get('quantidade') or '0'))
                    except (InvalidOperation, TypeError) as exc:
                        raise ValueError('Quantidade inválida.') from exc
                    produto = (
                        Produto.objects.for_filial(request.filial_ativa)
                        .vendaveis().filter(pk=item.get('produto_id'))
                        .first()
                    )
                    if not produto:
                        raise ValueError('Um dos produtos selecionados não está disponível.')
                    VendaService.adicionar_item(
                        pedido=pedido,
                        produto=produto,
                        quantidade=quantidade,
                    )

                pedido.status = PedidoVenda.Status.AGUARDANDO_APROVACAO
                pedido.save(update_fields=['status', 'updated_at'])
        except (DomainError, ValueError) as exc:
            return JsonResponse({'erro': str(exc)}, status=400)

        return JsonResponse({
            'ok': True,
            'pedido_id': pedido.pk,
            'numero_pedido': pedido.numero_pedido,
            'redirect_url': f'{reverse("vendas:pedido-detail", args=[pedido.pk])}?criado=1',
        })


class ClienteForcaVendasBuscaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'criar'

    def get(self, request):
        termo = (request.GET.get('q') or '').strip()
        qs = (
            Cliente.objects.for_filial(request.filial_ativa)
            .filter(ativo=True)
            .select_related('tabela_preco')
        )
        if termo:
            qs = qs.filter(
                Q(razao_social__icontains=termo)
                | Q(nome_fantasia__icontains=termo)
                | Q(cpf_cnpj__icontains=termo)
                | Q(celular__icontains=termo)
            )
        clientes = []
        for cliente in qs.order_by('razao_social')[:30]:
            limite = Decimal(cliente.limite_credito or 0)
            usado = Decimal(cliente.saldo_devedor or 0)
            clientes.append({
                'id': cliente.pk,
                'nome': cliente.nome_display,
                'razao_social': cliente.razao_social,
                'cpf_cnpj': cliente.cpf_cnpj or '',
                'cidade': cliente.cidade or '',
                'uf': cliente.uf or '',
                'bloqueado': cliente.bloqueado,
                'motivo_bloqueio': cliente.motivo_bloqueio or '',
                'limite_credito': float(limite),
                'saldo_devedor': float(usado),
                'credito_disponivel': float(max(Decimal('0'), limite - usado)),
                'tabela_preco': (
                    cliente.tabela_preco.descricao if cliente.tabela_preco_id
                    else 'Preço padrão'
                ),
                'prazo_pagamento_dias': cliente.prazo_pagamento_dias or 0,
            })
        return JsonResponse({'clientes': clientes})


class ProdutoForcaVendasBuscaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'criar'

    def get(self, request):
        termo = (request.GET.get('q') or '').strip()
        try:
            quantidade = Decimal(request.GET.get('quantidade') or '1')
        except InvalidOperation:
            quantidade = Decimal('1')
        if quantidade <= 0:
            quantidade = Decimal('1')
        cliente = (
            Cliente.objects.for_filial(request.filial_ativa)
            .filter(pk=request.GET.get('cliente_id'), ativo=True)
            .select_related('tabela_preco')
            .first()
        )
        if not cliente:
            return JsonResponse({'erro': 'Selecione o cliente antes dos produtos.'}, status=400)

        qs = Produto.objects.for_filial(request.filial_ativa).vendaveis()
        categoria_id = request.GET.get('categoria_id')
        if categoria_id:
            qs = qs.filter(Q(categoria_id=categoria_id) | Q(subcategoria_id=categoria_id))
        somente_destaques = request.GET.get('destaques') == '1'
        if somente_destaques:
            qs = qs.filter(catalogo_destaque=True)
        produto_id = request.GET.get('produto_id')
        if produto_id:
            qs = qs.filter(pk=produto_id)
        elif termo:
            qs = qs.filter(
                Q(descricao__icontains=termo)
                | Q(descricao_pdv__icontains=termo)
                | Q(codigo__icontains=termo)
                | Q(codigo_barras__icontains=termo)
            )

        produtos_consulta = list(qs.select_related('unidade_medida', 'categoria').order_by(
            '-catalogo_destaque', 'descricao',
        )[:30])
        anexar_saldos_por_deposito(
            produtos=produtos_consulta,
            filial=request.filial_ativa,
        )
        produtos = []
        for produto in produtos_consulta:
            contrato = ProdutoVendavelService.consultar(
                produto=produto,
                filial=request.filial_ativa,
                quantidade=quantidade,
                cliente=cliente,
                tabela_preco=cliente.tabela_preco,
                validar_promocoes=False,
                incluir_promocoes_aplicaveis=False,
            )
            produtos.append({
                'id': produto.pk,
                'descricao': contrato['descricao'],
                'codigo': produto.codigo or produto.codigo_barras or '',
                'unidade': produto.unidade_medida.sigla,
                'preco': float(contrato['preco_aplicado']),
                'preco_origem': contrato['preco_origem'],
                'estoque_disponivel': float(contrato['saldo_disponivel']),
                'estoques': [{
                    **saldo,
                    'atual': float(saldo['atual']),
                    'reservada': float(saldo['reservada']),
                    'disponivel': float(saldo['disponivel']),
                } for saldo in produto.saldos_depositos],
                'pode_vender': contrato['pode_vender'],
                'bloqueios': [item['label'] for item in contrato['bloqueios']],
                'categoria': produto.categoria.nome if produto.categoria_id else 'Sem categoria',
                'destaque': produto.catalogo_destaque,
                'foto_url': foto_produto_thumbnail_url(request, produto),
            })
        return JsonResponse({'produtos': produtos})


class PedidoKanbanView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    template_name = 'vendas/forca_vendas/kanban.html'

    COLUNAS = (
        ('analise', 'Em análise', (
            PedidoVenda.Status.RASCUNHO,
            PedidoVenda.Status.AGUARDANDO_APROVACAO,
            PedidoVenda.Status.APROVADO,
        )),
        ('aguardando_separacao', 'Aguardando separação', (PedidoVenda.Status.CONFIRMADO,)),
        ('separado', 'Separado', (PedidoVenda.Status.EM_SEPARACAO,)),
        ('faturado', 'Faturado', (
            PedidoVenda.Status.FATURADO,
            PedidoVenda.Status.PARCIALMENTE_FATURADO,
        )),
        ('entrega', 'Em entrega / Entregue', (
            PedidoVenda.Status.EM_ENTREGA,
            PedidoVenda.Status.ENTREGUE,
        )),
    )

    def get(self, request):
        qs = _pedidos_visiveis(request).exclude(status=PedidoVenda.Status.CANCELADO)
        termo = (request.GET.get('q') or '').strip()
        if termo:
            qs = qs.filter(
                Q(numero_pedido__icontains=termo)
                | Q(cliente__razao_social__icontains=termo)
                | Q(cliente__nome_fantasia__icontains=termo)
            )
        pedidos = list(qs.order_by('-data_emissao')[:250])
        colunas = []
        for chave, titulo, estados in self.COLUNAS:
            itens = [pedido for pedido in pedidos if pedido.status in estados]
            colunas.append({
                'chave': chave,
                'titulo': titulo,
                'pedidos': itens,
                'quantidade': len(itens),
                'valor_total': sum((pedido.valor_total for pedido in itens), Decimal('0')),
            })
        return render(request, self.template_name, {
            'title': 'Acompanhamento de pedidos',
            'colunas': colunas,
            'busca': termo,
        })


class AprovarPedidoExternoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'aprovar'

    def post(self, request, pk):
        pedido = get_object_or_404(
            PedidoVenda.objects.for_filial(request.filial_ativa),
            pk=pk,
            origem=PedidoVenda.Origem.EXTERNO,
        )
        try:
            VendaService.aprovar_e_confirmar_pedido(pedido, request.user)
            messages.success(
                request,
                f'Pedido {pedido.numero_pedido} liberado para separação.',
            )
        except DomainError as exc:
            messages.error(request, str(exc))
        return redirect('vendas:pedido-detail', pk=pedido.pk)


class PedidoSeparacaoFilaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'vendas'
    permissao_acao = 'editar'
    template_name = 'vendas/forca_vendas/separacao.html'

    def get(self, request):
        pedidos = list(
            PedidoVenda.objects.for_filial(request.filial_ativa)
            .filter(
                origem=PedidoVenda.Origem.EXTERNO,
                status__in=[
                    PedidoVenda.Status.CONFIRMADO,
                    PedidoVenda.Status.EM_SEPARACAO,
                ],
            )
            .select_related('cliente', 'usuario')
            .prefetch_related('itens__produto__unidade_medida', 'separacoes')
            .order_by('data_entrega_prevista', 'data_emissao')
        )
        depositos = list(
            Deposito.objects.filter(filial=request.filial_ativa, ativo=True)
            .order_by('-is_padrao', 'nome')
        )
        produtos = {
            item.produto_id: item.produto
            for pedido in pedidos for item in pedido.itens.all()
        }
        anexar_saldos_por_deposito(
            produtos=produtos.values(),
            filial=request.filial_ativa,
            depositos=depositos,
        )
        saldos_por_produto = {
            produto.pk: produto.saldos_depositos
            for produto in produtos.values()
        }
        for pedido in pedidos:
            for item in pedido.itens.all():
                item.saldos_depositos_tela = []
                for saldo in saldos_por_produto.get(item.produto_id, []):
                    saldo_tela = dict(saldo)
                    saldo_tela['disponivel_separacao'] = saldo['disponivel'] + (
                        (item.quantidade_reservada or Decimal('0'))
                        if saldo['origem_pedido'] else Decimal('0')
                    )
                    item.saldos_depositos_tela.append(saldo_tela)
        return render(request, self.template_name, {
            'title': 'Separação de pedidos',
            'pedidos_a_separar': [
                pedido for pedido in pedidos
                if pedido.status == PedidoVenda.Status.CONFIRMADO
            ],
            'pedidos_separados': [
                pedido for pedido in pedidos
                if pedido.status == PedidoVenda.Status.EM_SEPARACAO
            ],
            'depositos': depositos,
        })
