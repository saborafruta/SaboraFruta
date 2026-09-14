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
from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.authentication import SessionAuthentication
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.services.auditoria import registrar_auditoria
from apps.core.services.request_scope import empresa_operacional
from apps.produtos.models import Produto, ProdutoApresentacao

from .permissions import TemPermissaoProdutos
from .serializers import (
    ProdutoApresentacaoSerializer, ProdutoDetalheSerializer, ProdutoSerializer,
)

CAMPOS_CRITICOS_APRESENTACAO = frozenset({
    'fator_conversao', 'unidade', 'principal_venda', 'principal_compra',
})


class PaginacaoProdutos(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


class BaseProdutosAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [TemPermissaoProdutos]


class ProdutosView(BaseProdutosAPIView):
    """GET /api/produtos/produtos/ -- lista paginada e filtravel. POST cria."""

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
    """GET /api/produtos/produtos/{id}/"""

    @extend_schema(responses=ProdutoDetalheSerializer)
    def get(self, request, pk):
        empresa = empresa_operacional(request)
        produto = get_object_or_404(
            Produto.objects.for_empresa(empresa)
            .select_related('categoria', 'subcategoria', 'marca', 'fornecedor', 'unidade_medida')
            .prefetch_related('apresentacoes'),
            pk=pk,
        )
        return Response(ProdutoDetalheSerializer(produto).data)


class ProdutoApresentacoesView(BaseProdutosAPIView):
    """GET/POST /api/produtos/produtos/{id}/apresentacoes/"""

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
        return Response(ProdutoApresentacaoSerializer(queryset, many=True).data)

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
