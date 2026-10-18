from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from rest_framework import exceptions, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cadastros.models import Cliente, ClienteFilial
from apps.cadastros.services.cep_service import CepService
from apps.cadastros.services.cnpj_service import CnpjService
from apps.cadastros.services.cliente_service import ClienteService
from apps.core.models import EmpresaBanco, Filial
from apps.core.services.checkout import (
    checkout_venda_ativo,
    configuracao_descontos_checkout,
    desconto_checkout_requer_autorizacao,
    gerar_token_desconto_checkout_mobile,
    token_desconto_checkout_mobile_valido,
    validar_autorizador_checkout_por_email,
)
from apps.core.services.exceptions import DadosInvalidosError, DomainError, EstoqueInsuficienteError
from apps.core.tenant_context import tenant_atomic
from apps.estoque.models import Deposito, LoteProduto
from apps.estoque.services.saldos_por_deposito import anexar_saldos_por_deposito
from apps.financeiro.models import CondicaoPagamento, FormaPagamento
from apps.pdv.models import ItemVendaPDV, SessaoPDV, VendaPDV
from apps.pdv.services.produto_vendavel_service import ProdutoVendavelService
from apps.pdv.services.venda_pdv_service import VendaPDVService
from apps.produtos.models import CategoriaProduto, Produto
from apps.vendas.models import PedidoVenda, SeparacaoPedido
from apps.vendas.services.venda_service import VendaService

from .base import MobileTenantAPIView
from .serializers import (
    AutorizacaoDescontoCheckoutSerializer,
    CheckoutVendaSerializer,
    CheckoutPendenteSerializer,
    ClienteCompletoMobileSerializer,
    ClienteRapidoCheckoutSerializer,
    ConcluirSeparacaoSerializer,
    NovoPedidoSerializer,
    validar_idempotency_key,
)
from .throttling import MobileWriteThrottle


def _decimal(valor, casas=2):
    return f'{Decimal(valor or 0):.{casas}f}'


def _serializar_cliente_mobile(cliente):
    limite = Decimal(cliente.limite_credito or 0)
    usado = Decimal(cliente.saldo_devedor or 0)
    return {
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
        'tabela_preco_id': cliente.tabela_preco_id or 0,
        'prazo_pagamento_dias': cliente.prazo_pagamento_dias or 0,
    }


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
                    'checkout_vender': usuario.tem_permissao('pdv', 'ver'),
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
        limite = None if request.query_params.get('sync') == '1' else 50
        clientes = [
            _serializar_cliente_mobile(cliente)
            for cliente in qs.order_by('razao_social')[:limite]
        ]
        return Response({'clientes': clientes, 'sincronizado_em': timezone.now().isoformat()})

    def post(self, request):
        entrada = ClienteCompletoMobileSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        try:
            cliente = ClienteService.criar(
                entrada.validated_data,
                request.user,
                request.filial_ativa,
            )
        except DadosInvalidosError as exc:
            raise exceptions.ValidationError({'cliente': str(exc)}) from exc
        return Response(
            {'cliente': _serializar_cliente_mobile(cliente)},
            status=status.HTTP_201_CREATED,
        )


class ClienteCepMobileView(MobileTenantAPIView):
    permissao_modulo = 'vendas'
    permissao_acao = 'criar'

    def get(self, request):
        try:
            dados = CepService.consultar(request.query_params.get('cep', ''))
        except DadosInvalidosError as exc:
            raise exceptions.ValidationError({'cep': str(exc)}) from exc
        if dados is None:
            raise exceptions.NotFound('CEP não encontrado.')
        return Response({'endereco': dados})


class ClienteCnpjMobileView(MobileTenantAPIView):
    permissao_modulo = 'vendas'
    permissao_acao = 'criar'

    def get(self, request):
        try:
            dados = CnpjService.consultar(request.query_params.get('cnpj', ''))
        except DadosInvalidosError as exc:
            raise exceptions.ValidationError({'cnpj': str(exc)}) from exc
        if dados is None:
            raise exceptions.NotFound('CNPJ não encontrado ou serviço indisponível.')
        return Response({'empresa': dados})


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
        limite = None if request.query_params.get('sync') == '1' else 50
        produtos_consulta = list(
            qs.select_related('unidade_medida', 'categoria')
            .order_by('-catalogo_destaque', 'descricao')[:limite]
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
                'categoria_id': produto.categoria_id,
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
                        'permite_venda': saldo['permite_venda'],
                        'origem_pedido': saldo['origem_pedido'],
                    }
                    for saldo in produto.saldos_depositos
                ],
            })
        return Response({'produtos': produtos, 'sincronizado_em': timezone.now().isoformat()})


def _checkout_disponivel(request):
    if not checkout_venda_ativo(request):
        raise exceptions.NotFound('Checkout de venda não habilitado para esta filial.')


def _serializar_cliente_checkout(cliente):
    return {
        'id': cliente.pk,
        'nome': cliente.nome_display,
        'cpf_cnpj': cliente.cpf_cnpj or '',
        'cidade': cliente.cidade or '',
        'uf': cliente.uf or '',
        'bloqueado': cliente.bloqueado,
        'motivo_bloqueio': cliente.motivo_bloqueio or '',
        'credito_disponivel': _decimal(
            max(Decimal('0'), Decimal(cliente.limite_credito or 0) - Decimal(cliente.saldo_devedor or 0)),
        ),
        'tabela_preco': cliente.tabela_preco.descricao if cliente.tabela_preco_id else 'Preço padrão',
        'tabela_preco_id': cliente.tabela_preco_id or 0,
        'prazo_pagamento_dias': cliente.prazo_pagamento_dias or 0,
    }


class CheckoutEstadoMobileView(MobileTenantAPIView):
    permissao_modulo = 'pdv'
    permissao_acao = 'ver'

    def get(self, request):
        _checkout_disponivel(request)
        descontos = configuracao_descontos_checkout(request)
        sessao = (
            SessaoPDV.objects.for_filial(request.filial_ativa)
            .filter(usuario=request.user, status='aberto')
            .select_related('caixa')
            .first()
        )
        formas = (
            FormaPagamento.objects.filter(
                empresa=request.filial_ativa.empresa,
                ativo=True,
                exibir_no_pdv=True,
            )
            .filter(Q(filial=request.filial_ativa) | Q(filial__isnull=True))
            .order_by('descricao')
        )
        return Response({
            'checkout_ativo': True,
            'sessao': ({
                'id': sessao.pk,
                'caixa_id': sessao.caixa_id,
                'caixa_nome': str(sessao.caixa),
                'aberta_em': sessao.data_abertura.isoformat(),
            } if sessao else None),
            'formas_pagamento': [{
                'id': forma.pk,
                'descricao': forma.descricao,
                'tipo': forma.tipo,
            } for forma in formas],
            'pode_aplicar_desconto': True,
            'descontos': {
                'percentuais': [str(valor) for valor in descontos['percentuais']],
                'percentual_maximo': str(descontos['percentual_maximo']),
                'arredondamento_maximo': _decimal(descontos['arredondamento_maximo']),
            },
        })


class CheckoutDescontoAutorizacaoMobileView(MobileTenantAPIView):
    permissao_modulo = 'pdv'
    permissao_acao = 'ver'
    throttle_classes = [MobileWriteThrottle]

    def post(self, request):
        _checkout_disponivel(request)
        entrada = AutorizacaoDescontoCheckoutSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        autorizador = validar_autorizador_checkout_por_email(
            request,
            entrada.validated_data['email'],
            entrada.validated_data['senha'],
        )
        if autorizador is None:
            raise exceptions.PermissionDenied(
                'Credenciais inválidas ou usuário sem permissão PDV → Aprovar.'
            )
        return Response({
            'token': gerar_token_desconto_checkout_mobile(request, autorizador),
            'autorizado_por': autorizador.nome or autorizador.email,
        })


class CheckoutClientesMobileView(MobileTenantAPIView):
    permissao_modulo = 'pdv'
    permissao_acao = 'ver'

    def get(self, request):
        _checkout_disponivel(request)
        termo = request.query_params.get('q', '').strip()
        if len(termo) < 2:
            return Response({'clientes': []})
        filtro = (
            Q(razao_social__icontains=termo)
            | Q(nome_fantasia__icontains=termo)
            | Q(cpf_cnpj__icontains=termo)
        )
        if termo.isdigit():
            filtro |= Q(pk=int(termo))
        clientes = (
            Cliente.objects.for_filial(request.filial_ativa)
            .filter(ativo=True)
            .filter(filtro)
            .select_related('tabela_preco')
            .order_by('razao_social')[:20]
        )
        return Response({'clientes': [_serializar_cliente_checkout(cliente) for cliente in clientes]})

    def post(self, request):
        _checkout_disponivel(request)
        entrada = ClienteRapidoCheckoutSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        documento = dados['cpf_cnpj']
        if documento:
            existente = (
                Cliente.objects.for_filial(request.filial_ativa)
                .filter(cpf_cnpj=documento, ativo=True)
                .select_related('tabela_preco')
                .first()
            )
            if existente:
                return Response({'cliente': _serializar_cliente_checkout(existente), 'existente': True})
        tipo_pessoa = 'J' if len(documento) == 14 else 'F'
        with tenant_atomic():
            cliente = Cliente.objects.create(
                filial=request.filial_ativa,
                tipo_pessoa=tipo_pessoa,
                razao_social=dados['nome'].strip(),
                cpf_cnpj=documento,
                celular=dados['celular'].strip(),
                consumidor_final=True,
                ativo=True,
            )
            ClienteFilial.objects.create(
                cliente=cliente,
                filial=request.filial_ativa,
                ativo=True,
            )
        return Response(
            {'cliente': _serializar_cliente_checkout(cliente), 'existente': False},
            status=status.HTTP_201_CREATED,
        )


class CheckoutProdutosMobileView(MobileTenantAPIView):
    permissao_modulo = 'pdv'
    permissao_acao = 'ver'

    def get(self, request):
        _checkout_disponivel(request)
        termo = request.query_params.get('q', '').strip()
        if not termo:
            return Response({'produtos': []})
        cliente = None
        cliente_id = request.query_params.get('cliente_id', '').strip()
        if cliente_id:
            cliente = (
                Cliente.objects.for_filial(request.filial_ativa)
                .filter(pk=cliente_id, ativo=True)
                .select_related('tabela_preco')
                .first()
            )
            if cliente is None:
                raise exceptions.ValidationError({'cliente_id': 'Selecione um cliente válido.'})
        qs = Produto.objects.for_filial(request.filial_ativa).filter(ativo=True)
        filtro = (
            Q(descricao__icontains=termo)
            | Q(descricao_pdv__icontains=termo)
            | Q(codigo__icontains=termo)
            | Q(codigo_barras__icontains=termo)
        )
        if termo.isdigit():
            filtro |= Q(pk=int(termo))
        produtos_consulta = list(
            qs.filter(filtro)
            .select_related('unidade_medida', 'categoria')
            .order_by('descricao')[:20]
        )
        anexar_saldos_por_deposito(produtos=produtos_consulta, filial=request.filial_ativa)
        produtos = []
        for produto in produtos_consulta:
            contrato = ProdutoVendavelService.consultar(
                produto=produto,
                filial=request.filial_ativa,
                quantidade=Decimal('1'),
                cliente=cliente,
                tabela_preco=cliente.tabela_preco if cliente else None,
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
                'categoria': produto.categoria.nome if produto.categoria_id else 'Sem categoria',
                'categoria_id': produto.categoria_id,
                'destaque': produto.catalogo_destaque,
                'estoque_disponivel': _decimal(contrato['saldo_disponivel'], 3),
                'controla_lote': produto.controla_lote,
                'pode_vender': contrato['pode_vender'],
                'bloqueios': [item['label'] for item in contrato['bloqueios']],
                'estoques': [{
                    'deposito_id': saldo['id'],
                    'deposito_nome': saldo['nome'],
                    'disponivel': _decimal(saldo['disponivel'], 3),
                    'permite_venda': saldo['permite_venda'],
                    'origem_pedido': saldo['origem_pedido'],
                } for saldo in produto.saldos_depositos],
            })
        return Response({'produtos': produtos})


class CheckoutVendaMobileView(MobileTenantAPIView):
    permissao_modulo = 'pdv'
    permissao_acao = 'ver'
    throttle_classes = [MobileWriteThrottle]

    def post(self, request):
        _checkout_disponivel(request)
        chave = validar_idempotency_key(request)
        existente = VendaPDV.objects.for_filial(request.filial_ativa).filter(
            idempotency_key=chave,
        ).first()
        if existente:
            return Response({
                'venda_id': existente.pk,
                'numero_venda': existente.numero_venda,
                'valor_total': _decimal(existente.valor_total),
                'troco': _decimal(existente.troco),
                'repetida': True,
            })
        sessao = SessaoPDV.objects.for_filial(request.filial_ativa).filter(
            usuario=request.user,
            status='aberto',
        ).first()
        if sessao is None:
            raise exceptions.ValidationError({'sessao': 'Abra o caixa no iTED antes de finalizar a venda.'})
        entrada = CheckoutVendaSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        pendencia = None
        if dados.get('pendencia_id'):
            pendencia = (
                VendaPDV.objects.for_filial(request.filial_ativa)
                .filter(pk=dados['pendencia_id'], status='aberta')
                .first()
            )
            if pendencia is None:
                raise exceptions.ValidationError({'pendencia_id': 'Venda pendente não encontrada.'})
        desconto = dados.get('desconto') or Decimal('0')
        token_desconto_valido = token_desconto_checkout_mobile_valido(
            request,
            dados.get('autorizacao_desconto') or '',
        )
        try:
            with tenant_atomic():
                if pendencia is not None:
                    pendencia = (
                        VendaPDV.objects.for_filial(request.filial_ativa)
                        .select_for_update()
                        .get(pk=pendencia.pk, status='aberta')
                    )
                venda = VendaPDVService.finalizar_venda(
                    sessao=sessao,
                    filial=request.filial_ativa,
                    usuario=request.user,
                    itens=list(dados['itens']),
                    pagamentos=list(dados['pagamentos']),
                    cliente_id=dados.get('cliente_id'),
                    desconto=desconto,
                    observacao=dados.get('observacao') or '',
                    forcar_estoque_negativo=False,
                    idempotency_key=chave,
                    request=request,
                )
                autorizada_pela_pendencia = bool(
                    pendencia is not None
                    and desconto <= Decimal(pendencia.valor_desconto or 0)
                    and Decimal(venda.valor_subtotal or 0) >= Decimal(pendencia.valor_subtotal or 0)
                )
                if (
                    desconto_checkout_requer_autorizacao(request, venda)
                    and not token_desconto_valido
                    and not autorizada_pela_pendencia
                ):
                    raise exceptions.PermissionDenied(
                        'O desconto ultrapassa a política da loja. Autorize com um usuário '
                        'que tenha PDV → Aprovar.'
                    )
                if pendencia is not None:
                    pendencia.delete()
        except EstoqueInsuficienteError as exc:
            raise exceptions.ValidationError({'estoque': str(exc)}) from exc
        except DadosInvalidosError as exc:
            raise exceptions.ValidationError({'venda': str(exc)}) from exc
        except IntegrityError:
            existente = VendaPDV.objects.for_filial(request.filial_ativa).filter(
                idempotency_key=chave,
            ).first()
            if existente is None:
                raise
            venda = existente
        return Response({
            'venda_id': venda.pk,
            'numero_venda': venda.numero_venda,
            'valor_total': _decimal(venda.valor_total),
            'troco': _decimal(venda.troco),
            'repetida': False,
        }, status=status.HTTP_201_CREATED)


def _checkout_pendente_resumo(venda):
    return {
        'id': venda.pk,
        'numero_venda': str(venda.numero_venda),
        'cliente_nome': venda.cliente.nome_display if venda.cliente else 'Cliente balcão',
        'valor_total': _decimal(venda.valor_total),
        'desconto': _decimal(venda.valor_desconto),
        'data_venda': venda.data_venda.isoformat(),
        'quantidade_itens': len(venda.itens.all()),
    }


class CheckoutPendenciasMobileView(MobileTenantAPIView):
    permissao_modulo = 'pdv'
    permissao_acao = 'ver'
    throttle_classes = [MobileWriteThrottle]

    def get(self, request):
        _checkout_disponivel(request)
        vendas = list(
            VendaPDV.objects.for_filial(request.filial_ativa)
            .filter(status='aberta')
            .select_related('cliente')
            .prefetch_related('itens')
            .order_by('-data_venda')[:50]
        )
        return Response({'pendencias': [_checkout_pendente_resumo(venda) for venda in vendas]})

    def post(self, request):
        _checkout_disponivel(request)
        chave = validar_idempotency_key(request)
        existente = (
            VendaPDV.objects.for_filial(request.filial_ativa)
            .filter(idempotency_key=chave, status='aberta')
            .select_related('cliente')
            .prefetch_related('itens')
            .first()
        )
        if existente:
            return Response({**_checkout_pendente_resumo(existente), 'repetida': True})

        entrada = CheckoutPendenteSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        desconto = dados.get('desconto') or Decimal('0')
        token_desconto_valido = token_desconto_checkout_mobile_valido(
            request,
            dados.get('autorizacao_desconto') or '',
        )

        cliente = None
        if dados.get('cliente_id'):
            cliente = (
                Cliente.objects.for_filial(request.filial_ativa)
                .select_related('tabela_preco')
                .filter(pk=dados['cliente_id'], ativo=True)
                .first()
            )
            if cliente is None:
                raise exceptions.ValidationError({'cliente_id': 'Selecione um cliente válido.'})
        sessao = (
            SessaoPDV.objects.for_filial(request.filial_ativa)
            .filter(usuario=request.user, status='aberto')
            .first()
        )
        try:
            with tenant_atomic():
                venda = VendaPDV.objects.create(
                    sessao_pdv=sessao,
                    filial=request.filial_ativa,
                    numero_venda=VendaPDVService._proximo_numero_venda(request.filial_ativa),
                    cliente=cliente,
                    status='aberta',
                    origem='checkout_mobile',
                    pagamentos_rascunho=[
                        {'forma_id': item['forma_id'], 'valor': str(item['valor'])}
                        for item in dados.get('pagamentos', [])
                    ],
                    observacao=dados.get('observacao') or '',
                    valor_desconto=desconto,
                    usuario=request.user,
                    data_venda=timezone.now(),
                    idempotency_key=chave,
                )
                subtotal = Decimal('0')
                for numero, item in enumerate(dados['itens'], start=1):
                    produto = (
                        Produto.objects.for_filial(request.filial_ativa)
                        .select_related('unidade_medida')
                        .filter(pk=item['produto_id'], ativo=True)
                        .first()
                    )
                    if produto is None:
                        raise exceptions.ValidationError({'produto_id': 'Produto não encontrado.'})
                    quantidade = item['quantidade']
                    preco = VendaPDVService.resolver_preco_produto(
                        produto,
                        request.filial_ativa,
                        quantidade,
                        cliente,
                    )
                    valor_unitario = preco['preco']
                    valor_total = (quantidade * valor_unitario).quantize(Decimal('0.01'))
                    ItemVendaPDV.objects.create(
                        venda_pdv=venda,
                        produto=produto,
                        numero_item=numero,
                        quantidade=quantidade,
                        unidade_medida=produto.unidade_medida.sigla,
                        valor_unitario=valor_unitario,
                        valor_unitario_tabela=produto.preco_venda,
                        preco_origem=preco['tipo'],
                        preco_origem_detalhe=preco['origem'],
                        valor_total=valor_total,
                    )
                    subtotal += valor_total
                if desconto > subtotal:
                    raise exceptions.ValidationError({'desconto': 'O desconto não pode superar o total da venda.'})
                venda.valor_subtotal = subtotal
                venda.valor_total = subtotal - desconto
                venda.save(update_fields=['valor_subtotal', 'valor_total', 'updated_at'])
                if (
                    desconto_checkout_requer_autorizacao(request, venda)
                    and not token_desconto_valido
                ):
                    raise exceptions.PermissionDenied(
                        'O desconto ultrapassa a política da loja. Autorize com um usuário '
                        'que tenha PDV → Aprovar.'
                    )
        except IntegrityError:
            existente = (
                VendaPDV.objects.for_filial(request.filial_ativa)
                .filter(idempotency_key=chave, status='aberta')
                .select_related('cliente')
                .prefetch_related('itens')
                .first()
            )
            if existente is None:
                raise
            return Response({**_checkout_pendente_resumo(existente), 'repetida': True})
        venda = (
            VendaPDV.objects.for_filial(request.filial_ativa)
            .select_related('cliente')
            .prefetch_related('itens')
            .get(pk=venda.pk)
        )
        return Response({**_checkout_pendente_resumo(venda), 'repetida': False}, status=status.HTTP_201_CREATED)


class CheckoutPendenteDetalheMobileView(MobileTenantAPIView):
    permissao_modulo = 'pdv'
    permissao_acao = 'ver'

    def get(self, request, pk):
        _checkout_disponivel(request)
        venda = (
            VendaPDV.objects.for_filial(request.filial_ativa)
            .filter(pk=pk, status='aberta')
            .select_related('cliente__tabela_preco')
            .prefetch_related('itens__produto__unidade_medida', 'itens__produto__categoria')
            .first()
        )
        if venda is None:
            raise exceptions.NotFound('Venda pendente não encontrada.')
        produtos = [item.produto for item in venda.itens.all()]
        anexar_saldos_por_deposito(produtos=produtos, filial=request.filial_ativa)
        itens = []
        for item in venda.itens.all():
            produto = item.produto
            itens.append({
                'quantidade': _decimal(item.quantidade, 3),
                'produto': {
                    'id': produto.pk,
                    'descricao': produto.descricao_pdv or produto.descricao,
                    'codigo': produto.codigo or produto.codigo_barras or '',
                    'unidade': item.unidade_medida,
                    'preco': _decimal(item.valor_unitario),
                    'preco_origem': item.preco_origem_detalhe or item.preco_origem,
                    'categoria': produto.categoria.nome if produto.categoria_id else 'Sem categoria',
                    'categoria_id': produto.categoria_id,
                    'destaque': produto.catalogo_destaque,
                    'estoque_disponivel': _decimal(sum(
                        (saldo['disponivel'] for saldo in produto.saldos_depositos),
                        Decimal('0'),
                    ), 3),
                    'controla_lote': produto.controla_lote,
                    'pode_vender': produto.ativo,
                    'bloqueios': [],
                    'estoques': [{
                        'deposito_id': saldo['id'],
                        'deposito_nome': saldo['nome'],
                        'disponivel': _decimal(saldo['disponivel'], 3),
                        'permite_venda': saldo['permite_venda'],
                        'origem_pedido': saldo['origem_pedido'],
                    } for saldo in produto.saldos_depositos],
                },
            })
        pagamentos = venda.pagamentos_rascunho or []
        return Response({
            **_checkout_pendente_resumo(venda),
            'cliente': _serializar_cliente_checkout(venda.cliente) if venda.cliente else None,
            'desconto': _decimal(venda.valor_desconto),
            'forma_pagamento_id': pagamentos[0].get('forma_id') if pagamentos else None,
            'itens': itens,
        })


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
