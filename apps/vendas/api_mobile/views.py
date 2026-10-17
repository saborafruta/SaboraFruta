from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from rest_framework import exceptions, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cadastros.models import Cliente
from apps.core.models import EmpresaBanco, Filial
from apps.core.services.exceptions import DomainError
from apps.core.tenant_context import tenant_atomic
from apps.estoque.models import Deposito, LoteProduto
from apps.estoque.services.saldos_por_deposito import anexar_saldos_por_deposito
from apps.financeiro.models import CondicaoPagamento, FormaPagamento
from apps.pdv.services.produto_vendavel_service import ProdutoVendavelService
from apps.produtos.models import CategoriaProduto, Produto
from apps.vendas.models import PedidoVenda, SeparacaoPedido
from apps.vendas.services.venda_service import VendaService

from .base import MobileTenantAPIView
from .serializers import (
    ConcluirSeparacaoSerializer,
    NovoPedidoSerializer,
    validar_idempotency_key,
)
from .throttling import MobileWriteThrottle


def _decimal(valor, casas=2):
    return f'{Decimal(valor or 0):.{casas}f}'


def _pode_ver_equipe(usuario):
    return bool(
        usuario.is_superuser
        or usuario.perfil.is_admin
        or usuario.tem_permissao('vendas', 'aprovar')
        or usuario.tem_permissao('vendas', 'editar')
    )


def _serializar_pedido(pedido, detalhado=False):
    dados = {
        'id': pedido.pk,
        'numero_pedido': pedido.numero_pedido,
        'status': pedido.status,
        'status_descricao': pedido.get_status_display(),
        'prioridade': pedido.prioridade,
        'cliente_id': pedido.cliente_id,
        'cliente_nome': pedido.cliente.nome_display,
        'valor_total': _decimal(pedido.valor_total),
        'data_emissao': pedido.data_emissao.isoformat(),
        'data_entrega_prevista': (
            pedido.data_entrega_prevista.isoformat()
            if pedido.data_entrega_prevista else None
        ),
        'observacao': pedido.observacao,
    }
    if detalhado:
        dados['itens'] = [
            {
                'id': item.pk,
                'numero_item': item.numero_item,
                'produto_id': item.produto_id,
                'produto_descricao': item.produto.descricao,
                'produto_codigo': item.produto.codigo or item.produto.codigo_barras or '',
                'unidade': item.produto.unidade_medida.sigla,
                'quantidade': _decimal(item.quantidade, 3),
                'quantidade_solicitada': _decimal(
                    item.quantidade_solicitada or item.quantidade,
                    3,
                ),
                'quantidade_atendida': _decimal(item.quantidade_atendida, 3),
                'valor_unitario': _decimal(item.valor_unitario),
                'valor_total': _decimal(item.valor_total),
                'controla_lote': item.produto.controla_lote,
                'deposito_separacao_id': item.deposito_separacao_id,
            }
            for item in pedido.itens.all()
        ]
    return dados


class ContextoMobileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        usuario = request.user
        filiais = list(
            usuario.filiais_permitidas()
            .using('default')
            .select_related('empresa')
            .order_by('empresa__razao_social', '-is_matriz', 'razao_social')
        )
        bancos = {
            banco.empresa_id: banco
            for banco in EmpresaBanco.objects.using('default').filter(
                empresa_id__in={filial.empresa_id for filial in filiais},
                ativo=True,
                status=EmpresaBanco.Status.ATIVO,
            )
        }
        contextos = []
        for filial in filiais:
            banco = bancos.get(filial.empresa_id)
            if settings.TENANT_DATABASE_ROUTING_ENABLED and banco is None:
                continue
            perfil_anterior = getattr(usuario, '_perfil_ativo', None)
            usuario._perfil_ativo = usuario.perfil_para_filial(filial)
            try:
                permissoes = {
                    'pedidos_ver': usuario.tem_permissao('vendas', 'ver'),
                    'pedidos_criar': usuario.tem_permissao('vendas', 'criar'),
                    'separacao_editar': usuario.tem_permissao('vendas', 'editar'),
                }
            finally:
                if perfil_anterior is None:
                    usuario.__dict__.pop('_perfil_ativo', None)
                else:
                    usuario._perfil_ativo = perfil_anterior
            contextos.append({
                'tenant_alias': banco.db_alias if banco else 'default',
                'empresa_id': filial.empresa_id,
                'empresa_nome': filial.empresa.nome_fantasia or filial.empresa.razao_social,
                'filial_id': filial.pk,
                'filial_nome': filial.nome_fantasia or filial.razao_social,
                'filial_cnpj': filial.cnpj,
                'permissoes': permissoes,
            })
        return Response({
            'usuario': {
                'id': usuario.pk,
                'nome': usuario.nome,
                'email': usuario.email,
            },
            'contextos': contextos,
            'sincronizado_em': timezone.now().isoformat(),
        })


class ClientesMobileView(MobileTenantAPIView):
    permissao_modulo = 'vendas'
    permissao_acao = 'criar'

    def get(self, request):
        termo = request.query_params.get('q', '').strip()
        qs = (
            Cliente.objects.for_filial(request.filial_ativa)
            .filter(ativo=True)
            .select_related('tabela_preco')
        )
        if termo:
            filtro = (
                Q(razao_social__icontains=termo)
                | Q(nome_fantasia__icontains=termo)
                | Q(cpf_cnpj__icontains=termo)
                | Q(celular__icontains=termo)
            )
            if termo.isdigit():
                filtro |= Q(pk=int(termo))
            qs = qs.filter(filtro)
        clientes = []
        for cliente in qs.order_by('razao_social')[:50]:
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
                'limite_credito': _decimal(limite),
                'saldo_devedor': _decimal(usado),
                'credito_disponivel': _decimal(max(Decimal('0'), limite - usado)),
                'tabela_preco': (
                    cliente.tabela_preco.descricao
                    if cliente.tabela_preco_id else 'Preço padrão'
                ),
                'prazo_pagamento_dias': cliente.prazo_pagamento_dias or 0,
            })
        return Response({'clientes': clientes, 'sincronizado_em': timezone.now().isoformat()})


class ProdutosMobileView(MobileTenantAPIView):
    permissao_modulo = 'vendas'
    permissao_acao = 'criar'

    def get(self, request):
        cliente = (
            Cliente.objects.for_filial(request.filial_ativa)
            .filter(pk=request.query_params.get('cliente_id'), ativo=True)
            .select_related('tabela_preco')
            .first()
        )
        if cliente is None:
            raise exceptions.ValidationError({'cliente_id': 'Selecione um cliente válido.'})
        termo = request.query_params.get('q', '').strip()
        qs = Produto.objects.for_filial(request.filial_ativa).filter(ativo=True)
        categoria_id = request.query_params.get('categoria_id', '').strip()
        if categoria_id:
            try:
                categoria_id = int(categoria_id)
            except (TypeError, ValueError) as exc:
                raise exceptions.ValidationError({
                    'categoria_id': 'Selecione uma categoria válida.',
                }) from exc
            qs = qs.filter(
                Q(categoria_id=categoria_id) | Q(subcategoria_id=categoria_id)
            )
        if request.query_params.get('destaques') == '1':
            qs = qs.filter(catalogo_destaque=True)
        if termo:
            filtro = (
                Q(descricao__icontains=termo)
                | Q(descricao_pdv__icontains=termo)
                | Q(codigo__icontains=termo)
                | Q(codigo_barras__icontains=termo)
            )
            if termo.isdigit():
                filtro |= Q(pk=int(termo))
            qs = qs.filter(filtro)
        produtos_consulta = list(
            qs.select_related('unidade_medida', 'categoria')
            .order_by('-catalogo_destaque', 'descricao')[:50]
        )
        anexar_saldos_por_deposito(
            produtos=produtos_consulta,
            filial=request.filial_ativa,
        )
        produtos = []
        for produto in produtos_consulta:
            contrato = ProdutoVendavelService.consultar(
                produto=produto,
                filial=request.filial_ativa,
                quantidade=Decimal('1'),
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
                'preco': _decimal(contrato['preco_aplicado']),
                'preco_origem': contrato['preco_origem'],
                'categoria': (
                    produto.categoria.nome
                    if produto.categoria_id else 'Sem categoria'
                ),
                'destaque': produto.catalogo_destaque,
                'estoque_disponivel': _decimal(contrato['saldo_disponivel'], 3),
                'controla_lote': produto.controla_lote,
                'pode_vender': contrato['pode_vender'],
                'bloqueios': [item['label'] for item in contrato['bloqueios']],
                'estoques': [
                    {
                        'deposito_id': saldo['id'],
                        'deposito_nome': saldo['nome'],
                        'disponivel': _decimal(saldo['disponivel'], 3),
                    }
                    for saldo in produto.saldos_depositos
                ],
            })
        return Response({'produtos': produtos, 'sincronizado_em': timezone.now().isoformat()})


class OpcoesPedidoMobileView(MobileTenantAPIView):
    permissao_modulo = 'vendas'
    permissao_acao = 'criar'

    def get(self, request):
        formas = FormaPagamento.objects.filter(
            empresa=request.filial_ativa.empresa,
            ativo=True,
        ).filter(
            Q(filial=request.filial_ativa) | Q(filial__isnull=True)
        ).order_by('descricao')
        condicoes = CondicaoPagamento.objects.filter(
            empresa=request.filial_ativa.empresa,
            ativo=True,
        ).order_by('descricao')
        categorias = (
            CategoriaProduto.objects.for_filial(request.filial_ativa)
            .filter(ativo=True)
            .order_by('nome')
        )
        return Response({
            'formas_pagamento': [
                {'id': forma.pk, 'descricao': forma.descricao, 'tipo': forma.tipo}
                for forma in formas
            ],
            'condicoes_pagamento': [
                {
                    'id': condicao.pk,
                    'descricao': condicao.descricao,
                    'numero_parcelas': condicao.numero_parcelas,
                    'intervalo_dias': condicao.intervalo_dias,
                    'dias_primeira_parcela': condicao.dias_primeira_parcela,
                }
                for condicao in condicoes
            ],
            'categorias': [
                {'id': categoria.pk, 'nome': categoria.nome}
                for categoria in categorias
            ],
            'prioridades': [
                {'codigo': codigo, 'descricao': descricao}
                for codigo, descricao in PedidoVenda.Prioridade.choices
            ],
            'sincronizado_em': timezone.now().isoformat(),
        })


class PedidosMobileView(MobileTenantAPIView):
    permissao_modulo = 'vendas'
    throttle_classes = [MobileWriteThrottle]

    def get(self, request):
        qs = (
            PedidoVenda.objects.for_filial(request.filial_ativa)
            .filter(origem=PedidoVenda.Origem.EXTERNO)
            .select_related('cliente', 'usuario')
            .prefetch_related('itens__produto__unidade_medida')
        )
        if not _pode_ver_equipe(request.user):
            qs = qs.filter(usuario=request.user)
        pedidos = [_serializar_pedido(pedido, detalhado=True) for pedido in qs[:100]]
        return Response({'pedidos': pedidos, 'sincronizado_em': timezone.now().isoformat()})

    def post(self, request):
        self.permissao_acao = 'criar'
        if not request.user.tem_permissao('vendas', 'criar'):
            raise exceptions.PermissionDenied('Você não tem permissão para criar pedidos.')
        chave = validar_idempotency_key(request)
        existente = (
            PedidoVenda.objects.for_filial(request.filial_ativa)
            .filter(idempotency_key=chave)
            .select_related('cliente')
            .prefetch_related('itens__produto__unidade_medida')
            .first()
        )
        if existente:
            return Response(
                {'pedido': _serializar_pedido(existente, detalhado=True), 'repetido': True},
                status=status.HTTP_200_OK,
            )

        entrada = NovoPedidoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        cliente = (
            Cliente.objects.for_filial(request.filial_ativa)
            .filter(pk=dados['cliente_id'], ativo=True)
            .select_related('tabela_preco')
            .first()
        )
        if cliente is None:
            raise exceptions.ValidationError({'cliente_id': 'Selecione um cliente válido.'})
        forma = FormaPagamento.objects.filter(
            pk=dados['forma_pagamento_id'],
            empresa=request.filial_ativa.empresa,
            ativo=True,
        ).filter(Q(filial=request.filial_ativa) | Q(filial__isnull=True)).first()
        if forma is None:
            raise exceptions.ValidationError({
                'forma_pagamento_id': 'Selecione uma forma de pagamento válida.',
            })
        condicao = None
        if dados.get('condicao_pagamento_id'):
            condicao = CondicaoPagamento.objects.filter(
                pk=dados['condicao_pagamento_id'],
                empresa=request.filial_ativa.empresa,
                ativo=True,
            ).first()
            if condicao is None:
                raise exceptions.ValidationError({
                    'condicao_pagamento_id': 'Selecione uma condição de pagamento válida.',
                })

        try:
            with tenant_atomic():
                pedido = VendaService.criar_pedido(
                    filial=request.filial_ativa,
                    usuario=request.user,
                    cliente=cliente,
                    tipo=PedidoVenda.Tipo.PEDIDO,
                    observacao=dados.get('observacao', '').strip(),
                    idempotency_key=chave,
                )
                pedido.origem = PedidoVenda.Origem.EXTERNO
                pedido.prioridade = dados['prioridade']
                pedido.data_entrega_prevista = dados.get('data_entrega_prevista')
                pedido.observacao_interna = dados.get('observacao_interna', '').strip()
                pedido.forma_pagamento = forma
                pedido.condicao_pagamento = condicao
                pedido.prazo_pagamento_dias = dados['prazo_pagamento_dias']
                pedido.save(update_fields=[
                    'origem', 'prioridade', 'data_entrega_prevista',
                    'observacao_interna', 'forma_pagamento', 'condicao_pagamento',
                    'prazo_pagamento_dias', 'updated_at',
                ])
                for item in dados['itens']:
                    produto = (
                        Produto.objects.for_filial(request.filial_ativa)
                        .filter(pk=item['produto_id'], ativo=True)
                        .first()
                    )
                    if produto is None:
                        raise exceptions.ValidationError({
                            'itens': f'O produto {item["produto_id"]} não está disponível.',
                        })
                    VendaService.adicionar_item(
                        pedido=pedido,
                        produto=produto,
                        quantidade=item['quantidade'],
                    )
                pedido.status = PedidoVenda.Status.AGUARDANDO_APROVACAO
                pedido.save(update_fields=['status', 'updated_at'])
        except IntegrityError:
            pedido = PedidoVenda.objects.for_filial(request.filial_ativa).get(
                idempotency_key=chave,
            )
            pedido = (
                PedidoVenda.objects.select_related('cliente')
                .prefetch_related('itens__produto__unidade_medida')
                .get(pk=pedido.pk)
            )
            return Response(
                {'pedido': _serializar_pedido(pedido, detalhado=True), 'repetido': True},
                status=status.HTTP_200_OK,
            )
        except DomainError as exc:
            raise exceptions.ValidationError({'detail': str(exc)}) from exc

        pedido = (
            PedidoVenda.objects.select_related('cliente')
            .prefetch_related('itens__produto__unidade_medida')
            .get(pk=pedido.pk)
        )
        return Response(
            {'pedido': _serializar_pedido(pedido, detalhado=True), 'repetido': False},
            status=status.HTTP_201_CREATED,
        )


class SeparacoesMobileView(MobileTenantAPIView):
    permissao_modulo = 'vendas'
    permissao_acao = 'editar'

    def get(self, request):
        pedidos = list(
            PedidoVenda.objects.for_filial(request.filial_ativa)
            .filter(
                origem=PedidoVenda.Origem.EXTERNO,
                status__in=[PedidoVenda.Status.CONFIRMADO, PedidoVenda.Status.EM_SEPARACAO],
            )
            .select_related('cliente', 'usuario')
            .prefetch_related('itens__produto__unidade_medida', 'separacoes')
            .order_by('data_entrega_prevista', 'data_emissao')[:100]
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
        hoje = timezone.localdate()
        lotes_por_produto = {}
        for lote in LoteProduto.objects.filter(
            filial=request.filial_ativa,
            produto_id__in=produtos,
            status=LoteProduto.Status.ATIVO,
            quantidade_atual__gt=0,
        ).filter(Q(data_validade__isnull=True) | Q(data_validade__gte=hoje)).order_by(
            'produto_id', 'data_validade', 'created_at',
        ):
            lotes_por_produto.setdefault(lote.produto_id, []).append({
                'id': lote.pk,
                'numero': lote.numero_lote,
                'quantidade': _decimal(lote.quantidade_atual, 3),
                'validade': lote.data_validade.isoformat() if lote.data_validade else None,
            })

        fila = []
        for pedido in pedidos:
            dados = _serializar_pedido(pedido, detalhado=False)
            dados['itens'] = []
            for item in pedido.itens.all():
                saldo_produto = {
                    saldo['id']: saldo for saldo in getattr(item.produto, 'saldos_depositos', [])
                }
                dados['itens'].append({
                    'id': item.pk,
                    'numero_item': item.numero_item,
                    'produto_id': item.produto_id,
                    'produto_descricao': item.produto.descricao,
                    'produto_codigo': item.produto.codigo or item.produto.codigo_barras or '',
                    'unidade': item.produto.unidade_medida.sigla,
                    'quantidade_solicitada': _decimal(
                        item.quantidade_solicitada or item.quantidade,
                        3,
                    ),
                    'controla_lote': item.produto.controla_lote,
                    'lote_modo': 'manual_ou_fefo' if item.produto.controla_lote else 'sem_lote',
                    'lotes_sugeridos': lotes_por_produto.get(item.produto_id, []),
                    'depositos': [
                        {
                            'id': deposito.pk,
                            'nome': deposito.nome,
                            'disponivel': _decimal(
                                saldo_produto.get(deposito.pk, {}).get('disponivel', 0),
                                3,
                            ),
                            'padrao': deposito.is_padrao,
                        }
                        for deposito in depositos
                    ],
                })
            separacao = pedido.separacoes.order_by('-data_inicio').first()
            dados['separacao'] = (
                {
                    'id': separacao.pk,
                    'numero': separacao.numero,
                    'status': separacao.status,
                    'concluida_em': separacao.data_fim.isoformat() if separacao.data_fim else None,
                }
                if separacao else None
            )
            fila.append(dados)
        return Response({'pedidos': fila, 'sincronizado_em': timezone.now().isoformat()})


class ConcluirSeparacaoMobileView(MobileTenantAPIView):
    permissao_modulo = 'vendas'
    permissao_acao = 'editar'
    throttle_classes = [MobileWriteThrottle]

    def post(self, request, pedido_id):
        chave = validar_idempotency_key(request)
        existente = (
            SeparacaoPedido.objects.for_filial(request.filial_ativa)
            .filter(idempotency_key=chave)
            .select_related('pedido')
            .first()
        )
        if existente:
            return Response({
                'separacao_id': existente.pk,
                'numero': existente.numero,
                'pedido_id': existente.pedido_id,
                'pedido_status': existente.pedido.status,
                'repetido': True,
            })
        entrada = ConcluirSeparacaoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        pedido = (
            PedidoVenda.objects.for_filial(request.filial_ativa)
            .filter(
                pk=pedido_id,
                origem=PedidoVenda.Origem.EXTERNO,
                status=PedidoVenda.Status.CONFIRMADO,
            )
            .first()
        )
        if pedido is None:
            raise exceptions.ValidationError({
                'pedido': 'O pedido não está disponível para separação.',
            })
        try:
            separacao = VendaService.separar_pedido(
                pedido=pedido,
                usuario=request.user,
                itens_conferidos=list(entrada.validated_data['itens']),
                observacao=entrada.validated_data.get('observacao', ''),
                idempotency_key=chave,
            )
        except IntegrityError:
            separacao = SeparacaoPedido.objects.for_filial(request.filial_ativa).get(
                idempotency_key=chave,
            )
            repetido = True
        except DomainError as exc:
            raise exceptions.ValidationError({'detail': str(exc)}) from exc
        else:
            repetido = False
        pedido.refresh_from_db()
        return Response({
            'separacao_id': separacao.pk,
            'numero': separacao.numero,
            'pedido_id': pedido.pk,
            'pedido_status': pedido.status,
            'repetido': repetido,
        }, status=status.HTTP_200_OK if repetido else status.HTTP_201_CREATED)
