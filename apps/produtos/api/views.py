"""
API REST de cadastro de produtos e apresentacoes.

Autenticacao: sessao do proprio ERP -- mesmo motivo/mesma decisao ja
documentada em `apps/estoque/api/urls.py` (arquitetura multi-tenant: nao
ha login JWT tenant-aware ainda). RBAC via `TemPermissaoProdutos`, que
reaproveita `usuario.tem_permissao('produtos', acao)`.

Montada em `/api/produtos/`, NAO em `/api/v1/` -- esse prefixo ja e' da
API de integracoes (`apps.integracoes`, autenticacao por API-key, publico
externo) e ja registra `produtos/`/`produtos/<pk>/` com outro proposito e
outro schema. Reaproveitar o mesmo prefixo colidiria com rotas existentes
e misturaria dois esquemas de autenticacao sob o mesmo namespace.
"""
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.authentication import SessionAuthentication
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Filial
from apps.core.services.auditoria import registrar_auditoria
from apps.core.services.request_scope import empresa_operacional
from apps.core.tenant_context import get_current_database_alias
from apps.estoque.models import Estoque
from apps.produtos.models import ItemTabelaPreco, Produto, ProdutoApresentacao, ProdutoCodigoBarras, TabelaPreco
from apps.produtos.services.apresentacao_service import ApresentacaoService

from .exceptions import erro_response as _erro
from .exceptions import formatar_erros_api
from .permissions import TemPermissaoProdutos
from .serializers import (
    EstoqueSerializer, ItemTabelaPrecoSerializer, LookupCodigoBarrasSerializer,
    ProdutoApresentacaoSerializer, ProdutoDetalheSerializer, ProdutoSerializer,
)
from .throttling import ThrottleLookupCodigoBarras

TTL_LOOKUP_ENCONTRADO = 60
TTL_LOOKUP_NAO_ENCONTRADO = 30

CAMPOS_CRITICOS_APRESENTACAO = frozenset({
    'fator_conversao', 'unidade', 'principal_venda', 'principal_compra', 'preco_venda', 'ativo',
})
CAMPOS_CRITICOS_PRODUTO = frozenset({'unidade_medida', 'codigo', 'codigo_barras', 'ativo'})


class PaginacaoProdutos(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


class BaseProdutosAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [TemPermissaoProdutos]

    def get_exception_handler(self):
        return formatar_erros_api


class ProdutosView(BaseProdutosAPIView):
    """GET /api/produtos/produtos/ -- lista paginada e filtravel. POST cria."""

    pagination_class = PaginacaoProdutos

    def get_queryset(self, empresa):
        queryset = (
            Produto.objects.for_empresa(empresa)
            .select_related('categoria', 'subcategoria', 'marca', 'fornecedor', 'unidade_medida')
            .prefetch_related('apresentacoes')
        )
        ativo = self.request.query_params.get('ativo')
        if ativo is not None:
            queryset = queryset.filter(ativo=ativo.lower() in ('1', 'true', 'sim'))
        codigo = self.request.query_params.get('codigo')
        if codigo:
            queryset = queryset.filter(codigo__iexact=codigo)
        return queryset.order_by('descricao')

    @extend_schema(responses=ProdutoSerializer(many=True))
    def get(self, request):
        empresa = empresa_operacional(request)
        queryset = self.get_queryset(empresa)
        busca = request.query_params.get('busca') or request.query_params.get('search')
        if busca:
            queryset = queryset.filter(descricao__icontains=busca)
        paginador = PaginacaoProdutos()
        pagina = paginador.paginate_queryset(queryset, request, view=self)
        return paginador.get_paginated_response(ProdutoSerializer(pagina, many=True).data)

    @extend_schema(request=ProdutoSerializer, responses={201: ProdutoSerializer})
    def post(self, request):
        filial = getattr(request, 'filial_ativa', None)
        if filial is None:
            return Response({'detail': 'Nenhuma filial ativa selecionada nesta sessao.'}, status=400)

        serializer = ProdutoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        produto = serializer.save(filial=filial)
        registrar_auditoria(
            request=request, modulo='produtos', acao='criar', objeto=produto,
            descricao=str(produto), depois=serializer.data,
        )
        return Response(ProdutoSerializer(produto).data, status=201)


class ProdutoDetalheView(BaseProdutosAPIView):
    """GET/PATCH /api/produtos/produtos/{id}/

    PATCH audita explicitamente quando um campo critico e alterado
    (unidade_medida -- a unidade BASE do produto, da qual todo fator de
    conversao de apresentacao depende --, codigo, codigo_barras, ativo).
    Mesmo mecanismo de `ApresentacaoDetalheView.patch`."""

    def get_object(self, empresa, pk):
        return get_object_or_404(
            Produto.objects.for_empresa(empresa)
            .select_related('categoria', 'subcategoria', 'marca', 'fornecedor', 'unidade_medida')
            .prefetch_related('apresentacoes'),
            pk=pk,
        )

    @extend_schema(responses=ProdutoDetalheSerializer)
    def get(self, request, pk):
        empresa = empresa_operacional(request)
        produto = self.get_object(empresa, pk)
        return Response(ProdutoDetalheSerializer(produto).data)

    @extend_schema(request=ProdutoSerializer, responses=ProdutoDetalheSerializer)
    def patch(self, request, pk):
        empresa = empresa_operacional(request)
        produto = self.get_object(empresa, pk)
        antes = ProdutoSerializer(produto).data

        serializer = ProdutoSerializer(produto, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        nova_unidade = serializer.validated_data.get('unidade_medida')
        if nova_unidade is not None and nova_unidade != produto.unidade_medida and produto.apresentacoes.exists():
            return _erro(
                'Nao e possivel trocar a unidade base de um produto que ja tem apresentacoes '
                'cadastradas -- o fator_conversao de cada uma e absoluto e relativo a unidade base '
                'atual; trocar a unidade sem recalcular cada fator corromperia a conversao '
                'silenciosamente. Inative as apresentacoes atuais e recrie-as na unidade nova.',
                codigo='unidade_base_com_apresentacoes', campo='unidade_medida',
            )

        campos_alterados_criticos = CAMPOS_CRITICOS_PRODUTO & set(serializer.validated_data)
        with transaction.atomic():
            produto = serializer.save()
            if campos_alterados_criticos:
                registrar_auditoria(
                    request=request, modulo='produtos', acao='editar', objeto=produto,
                    descricao=f'Campo(s) critico(s) alterado(s): {", ".join(sorted(campos_alterados_criticos))}',
                    antes=antes, depois=ProdutoSerializer(produto).data,
                )
        return Response(ProdutoDetalheSerializer(produto).data)


class ProdutoApresentacoesView(BaseProdutosAPIView):
    """GET/POST /api/produtos/produtos/{id}/apresentacoes/"""

    pagination_class = PaginacaoProdutos

    @extend_schema(responses=ProdutoApresentacaoSerializer(many=True))
    def get(self, request, pk):
        empresa = empresa_operacional(request)
        produto = get_object_or_404(Produto.objects.for_empresa(empresa), pk=pk)
        queryset = produto.apresentacoes.select_related('unidade').order_by('fator_conversao')
        ativo = request.query_params.get('ativo')
        if ativo is not None:
            queryset = queryset.filter(ativo=ativo.lower() in ('1', 'true', 'sim'))
        permite_venda = request.query_params.get('permite_venda')
        if permite_venda is not None:
            queryset = queryset.filter(permite_venda=permite_venda.lower() in ('1', 'true', 'sim'))
        paginador = PaginacaoProdutos()
        pagina = paginador.paginate_queryset(queryset, request, view=self)
        return paginador.get_paginated_response(ProdutoApresentacaoSerializer(pagina, many=True).data)

    @extend_schema(request=ProdutoApresentacaoSerializer, responses={201: ProdutoApresentacaoSerializer})
    def post(self, request, pk):
        empresa = empresa_operacional(request)
        produto = get_object_or_404(Produto.objects.for_empresa(empresa), pk=pk)

        serializer = ProdutoApresentacaoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            apresentacao = serializer.save(produto=produto)
        registrar_auditoria(
            request=request, modulo='produtos', acao='criar', objeto=apresentacao,
            descricao=str(apresentacao), relacionado=produto, depois=serializer.data,
        )
        return Response(ProdutoApresentacaoSerializer(apresentacao).data, status=201)


class ApresentacaoDetalheView(BaseProdutosAPIView):
    """PATCH /api/produtos/apresentacoes/{id}/ -- alteracao parcial, com
    auditoria explicita quando um campo critico (fator, unidade, flag
    principal_*) e alterado (ver CAMPOS_CRITICOS_APRESENTACAO)."""

    def get_object(self, request, pk):
        empresa = empresa_operacional(request)
        return get_object_or_404(
            ProdutoApresentacao.objects.select_related('produto', 'unidade').filter(
                produto__in=Produto.objects.for_empresa(empresa),
            ),
            pk=pk,
        )

    @extend_schema(responses=ProdutoApresentacaoSerializer)
    def get(self, request, pk):
        apresentacao = self.get_object(request, pk)
        return Response(ProdutoApresentacaoSerializer(apresentacao).data)

    @extend_schema(request=ProdutoApresentacaoSerializer, responses=ProdutoApresentacaoSerializer)
    def patch(self, request, pk):
        apresentacao = self.get_object(request, pk)
        antes = ProdutoApresentacaoSerializer(apresentacao).data

        serializer = ProdutoApresentacaoSerializer(apresentacao, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        campos_alterados_criticos = CAMPOS_CRITICOS_APRESENTACAO & set(serializer.validated_data)
        with transaction.atomic():
            apresentacao = serializer.save()
            if campos_alterados_criticos:
                registrar_auditoria(
                    request=request, modulo='produtos', acao='editar', objeto=apresentacao,
                    descricao=f'Campo(s) critico(s) alterado(s): {", ".join(sorted(campos_alterados_criticos))}',
                    relacionado=apresentacao.produto, antes=antes,
                    depois=ProdutoApresentacaoSerializer(apresentacao).data,
                )
        return Response(ProdutoApresentacaoSerializer(apresentacao).data)


class ProdutoPrecosView(BaseProdutosAPIView):
    """GET /api/produtos/produtos/{id}/precos/ -- itens de tabela de preco do produto.

    Filtros opcionais: `?filial=<id>` (so tabelas vinculadas aquela filial,
    via `TabelaPreco.objects.for_filial`, mesmo manager usado no resto do
    ERP) e `?apresentacao=<id>` (so precos daquela apresentacao especifica;
    `apresentacao=null` -- literal -- traz so os precos "default", no nivel
    do produto)."""

    pagination_class = PaginacaoProdutos

    @extend_schema(responses=ItemTabelaPrecoSerializer(many=True))
    def get(self, request, pk):
        empresa = empresa_operacional(request)
        produto = get_object_or_404(Produto.objects.for_empresa(empresa), pk=pk)

        queryset = ItemTabelaPreco.objects.filter(produto=produto).select_related('tabela', 'apresentacao')
        filial_id = request.query_params.get('filial')
        if filial_id:
            filial = get_object_or_404(Filial, pk=filial_id, empresa=empresa)
            tabelas_da_filial = TabelaPreco.objects.for_filial(filial).values_list('pk', flat=True)
            queryset = queryset.filter(tabela_id__in=tabelas_da_filial)
        apresentacao_id = request.query_params.get('apresentacao')
        if apresentacao_id == 'null':
            queryset = queryset.filter(apresentacao__isnull=True)
        elif apresentacao_id:
            queryset = queryset.filter(apresentacao_id=apresentacao_id)
        queryset = queryset.filter(tabela__ativo=True).order_by('tabela', 'quantidade_minima')

        paginador = PaginacaoProdutos()
        pagina = paginador.paginate_queryset(queryset, request, view=self)
        return paginador.get_paginated_response(ItemTabelaPrecoSerializer(pagina, many=True).data)


class ProdutoEstoqueView(BaseProdutosAPIView):
    """GET /api/produtos/produtos/{id}/estoque/ -- saldo por filial x deposito.

    Filtro opcional `?filial=<id>`."""

    pagination_class = PaginacaoProdutos

    @extend_schema(responses=EstoqueSerializer(many=True))
    def get(self, request, pk):
        empresa = empresa_operacional(request)
        produto = get_object_or_404(Produto.objects.for_empresa(empresa), pk=pk)

        queryset = Estoque.objects.filter(
            produto=produto, filial__empresa=empresa,
        ).select_related('filial', 'deposito')
        filial_id = request.query_params.get('filial')
        if filial_id:
            queryset = queryset.filter(filial_id=filial_id)
        queryset = queryset.order_by('filial', 'deposito')

        paginador = PaginacaoProdutos()
        pagina = paginador.paginate_queryset(queryset, request, view=self)
        return paginador.get_paginated_response(EstoqueSerializer(pagina, many=True).data)


def _resolver_produto_apresentacao(empresa, codigo):
    """Resolve (produto, apresentacao) a partir de um EAN ou codigo interno.

    Ordem: EAN cadastrado em ProdutoCodigoBarras (usando a apresentacao
    vinculada a ele, se houver; senao a principal_venda do produto) ->
    codigo interno ou codigo_barras "principal" do Produto (usando a
    apresentacao principal_venda). Nao varre `codigos_barras_extras`
    (JSON, exige scan Python) -- para esse caso raro, o lookup existente
    do PDV (`apps/pdv/views/pdv.py`) continua a via.
    """
    codigo_barras = (
        ProdutoCodigoBarras.objects.select_related('produto', 'apresentacao')
        .filter(ean=codigo, ativo=True, produto__in=Produto.objects.for_empresa(empresa))
        .first()
    )
    if codigo_barras:
        apresentacao = codigo_barras.apresentacao or ApresentacaoService.apresentacao_principal_venda(
            codigo_barras.produto,
        )
        if apresentacao:
            return codigo_barras.produto_id, apresentacao.pk

    produto = (
        Produto.objects.for_empresa(empresa)
        .filter(Q(codigo=codigo) | Q(codigo_barras=codigo))
        .first()
    )
    if produto:
        apresentacao = ApresentacaoService.apresentacao_principal_venda(produto)
        if apresentacao:
            return produto.pk, apresentacao.pk

    return None


class LookupCodigoBarrasView(BaseProdutosAPIView):
    """
    POST /api/produtos/lookup-codigo-barras/{codigo}/ -- resolve
    produto+apresentacao a partir de um EAN/codigo bipado, numa unica
    chamada. Endpoint dedicado e novo: NAO substitui nem altera o lookup
    ja existente em `apps/pdv/views/pdv.py`, que continua rodando em
    producao sem mudanca (decisao registrada -- ver conversa/CLAUDE.md).

    Cache: so a RESOLUCAO (produto_id, apresentacao_id) e' cacheada -- o
    par produto/EAN muda raramente. `preco` e `estoque_disponivel` sao
    SEMPRE lidos ao vivo a cada chamada, nunca cacheados: servir estoque
    ou preco desatualizado pro PDV pode causar venda sem saldo ou com
    preco errado. Chave de cache inclui o alias do banco do tenant (cada
    empresa tem seu proprio banco -- ver apps/core/tenant_context.py) e o
    id da empresa, para nunca vazar resolucao de uma empresa pra outra
    caso o mesmo Redis seja compartilhado entre tenants.

    Sem invalidacao ativa (nao instrumenta todo caminho de escrita que
    toca EAN/apresentacao principal -- admin, forms HTML, esta API).
    TTL curto (60s) em vez disso: uma mudanca de EAN demora no maximo
    esse tempo pra refletir no PDV. Documentado aqui de proposito --
    revisar se algum fluxo precisar de consistencia mais forte.
    """

    permissao_acao = 'ver'  # POST aqui e' consulta, nao criacao
    throttle_classes = [ThrottleLookupCodigoBarras]

    @extend_schema(request=None, responses={200: LookupCodigoBarrasSerializer, 404: None})
    def post(self, request, codigo):
        empresa = empresa_operacional(request)
        resolucao = self._resolver_com_cache(empresa, codigo)
        if resolucao is None:
            return Response({'detail': 'Codigo nao encontrado.'}, status=404)

        produto_id, apresentacao_id = resolucao
        produto = get_object_or_404(Produto, pk=produto_id)
        apresentacao = get_object_or_404(
            ProdutoApresentacao.objects.select_related('unidade'), pk=apresentacao_id,
        )

        filial = getattr(request, 'filial_ativa', None)
        estoque_disponivel = None
        if filial is not None:
            estoque_disponivel = Estoque.objects.filter(
                produto=produto, filial=filial,
            ).aggregate(total=Sum('quantidade_disponivel'))['total'] or Decimal('0')

        dados = {
            'produto': {'id': produto.pk, 'codigo': produto.codigo, 'descricao': produto.descricao},
            'apresentacao': {'id': apresentacao.pk, 'descricao': apresentacao.descricao},
            'unidade': {'id': apresentacao.unidade_id, 'sigla': apresentacao.unidade.sigla},
            'fator': apresentacao.fator_conversao,
            'preco': apresentacao.preco_venda,
            'estoque_disponivel': estoque_disponivel,
        }
        return Response(LookupCodigoBarrasSerializer(dados).data)

    def _resolver_com_cache(self, empresa, codigo):
        chave = f'produtos:lookup_ean:{get_current_database_alias()}:{empresa.pk}:{codigo}'
        cacheado = cache.get(chave)
        if cacheado is not None:
            return tuple(cacheado) if cacheado else None

        resolucao = _resolver_produto_apresentacao(empresa, codigo)
        cache.set(
            chave, list(resolucao) if resolucao else [],
            TTL_LOOKUP_ENCONTRADO if resolucao else TTL_LOOKUP_NAO_ENCONTRADO,
        )
        return resolucao
