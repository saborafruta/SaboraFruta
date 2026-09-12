from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import exceptions
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cadastros.models import Cliente
from apps.core.models import Filial
from apps.core.tenant_context import reset_current_tenant_db, set_current_tenant_db
from apps.estoque.models import Estoque
from apps.moda.models import OrdemProducao, ProdutoModa, Variante
from apps.produtos.models import Produto

from .authentication import ChaveApiAuthentication
from .models import ESCOPOS_DISPONIVEIS
from .pagination import PaginacaoIntegracao
from .permissions import PossuiEscopoIntegracao
from .serializers import (
    FilialSerializer,
    ClienteSerializer,
    EstoqueSerializer,
    OrdemProducaoDetalheSerializer,
    OrdemProducaoSerializer,
    ProdutoModaSerializer,
    ProdutoSerializer,
    VarianteModaSerializer,
    url_absoluta,
)
from .throttling import LimitePorCredencial


class BaseIntegracaoView:
    authentication_classes = [ChaveApiAuthentication]
    permission_classes = [IsAuthenticated, PossuiEscopoIntegracao]
    throttle_classes = [LimitePorCredencial]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self._tenant_token = set_current_tenant_db(self.alias)

    def finalize_response(self, request, response, *args, **kwargs):
        try:
            return super().finalize_response(request, response, *args, **kwargs)
        finally:
            token = getattr(self, '_tenant_token', None)
            if token is not None:
                reset_current_tenant_db(token)

    @property
    def alias(self):
        return self.request.integracao_alias

    def filiais_permitidas(self):
        credencial = self.request.auth
        centrais = Filial.objects.using('default').filter(
            empresa_id=credencial.empresa_id,
            empresa__ativo=True,
            ativo=True,
        )
        # A restrição da credencial vive no banco central. O contexto do tenant
        # já está ativo neste ponto, então o alias precisa ser explícito para a
        # tabela intermediária não ser procurada no banco operacional.
        ids_restritos = list(
            credencial.filiais.using('default').values_list('pk', flat=True)
        )
        if ids_restritos:
            centrais = centrais.filter(pk__in=ids_restritos)
        cnpjs = list(centrais.values_list('cnpj', flat=True))
        return (
            Filial.objects.using(self.alias)
            .select_related('empresa')
            .filter(cnpj__in=cnpjs, ativo=True)
        )

    def filiais_ids(self):
        return list(self.filiais_permitidas().values_list('pk', flat=True))

    def filtrar_filial(self, queryset):
        filial_cnpj = self.request.query_params.get('filial_cnpj', '').strip()
        filiais = self.filiais_permitidas()
        if filial_cnpj:
            filiais = filiais.filter(cnpj=filial_cnpj)
        return queryset.filter(filial_id__in=filiais.values('pk'))

    def filtrar_atualizacao(self, queryset):
        valor = self.request.query_params.get('atualizado_desde', '').strip()
        if not valor:
            return queryset
        instante = parse_datetime(valor)
        if not instante:
            raise exceptions.ValidationError({
                'atualizado_desde': 'Use uma data ISO 8601, por exemplo 2026-09-12T10:30:00Z.',
            })
        if timezone.is_naive(instante):
            instante = timezone.make_aware(instante)
        return queryset.filter(updated_at__gte=instante)


class RaizApiView(BaseIntegracaoView, APIView):
    def get(self, request):
        def url(nome):
            return request.build_absolute_uri(reverse(f'integracoes_api:{nome}'))

        return Response({
            'api': 'iTed Integrações',
            'versao': 'v1',
            'empresa': {
                'id': request.auth.empresa_id,
                'razao_social': request.auth.empresa.razao_social,
                'nome_fantasia': request.auth.empresa.nome_fantasia,
                'cnpj': request.auth.empresa.cnpj,
                'logo_url': url_absoluta(request, request.auth.empresa.logo_url),
            },
            'credencial': request.auth.nome,
            'escopos': request.auth.escopos,
            'recursos': {
                'contexto': url('contexto'),
                'filiais': url('filiais'),
                'clientes': url('clientes'),
                'produtos': url('produtos'),
                'estoque': url('estoque'),
                'produtos_moda': url('produtos_moda'),
                'variantes_moda': url('variantes_moda'),
                'ordens_producao': url('ordens_producao'),
            },
        })


class ContextoApiView(BaseIntegracaoView, APIView):
    def get(self, request):
        return Response({
            'empresa': {
                'id': request.auth.empresa_id,
                'razao_social': request.auth.empresa.razao_social,
                'nome_fantasia': request.auth.empresa.nome_fantasia,
                'cnpj': request.auth.empresa.cnpj,
                'logo_url': url_absoluta(request, request.auth.empresa.logo_url),
            },
            'filiais': FilialSerializer(
                self.filiais_permitidas(), many=True, context={'request': request},
            ).data,
            'escopos': [
                {'codigo': codigo, 'descricao': ESCOPOS_DISPONIVEIS.get(codigo, codigo)}
                for codigo in request.auth.escopos
            ],
        })


class FiliaisApiView(BaseIntegracaoView, GenericAPIView):
    escopo_necessario = 'filiais:ler'
    serializer_class = FilialSerializer
    pagination_class = PaginacaoIntegracao

    def get(self, request):
        qs = self.filiais_permitidas().order_by('nome_fantasia', 'razao_social')
        pagina = self.paginate_queryset(qs)
        return self.get_paginated_response(self.get_serializer(pagina, many=True).data)


class ProdutosApiView(BaseIntegracaoView, GenericAPIView):
    escopo_necessario = 'produtos:ler'
    serializer_class = ProdutoSerializer
    pagination_class = PaginacaoIntegracao

    def get_queryset(self):
        qs = Produto.objects.using(self.alias).select_related(
            'filial__empresa', 'unidade_medida', 'categoria',
            'subcategoria', 'marca',
        )
        qs = self.filtrar_filial(qs)
        qs = self.filtrar_atualizacao(qs)
        busca = self.request.query_params.get('busca', '').strip()
        if busca:
            qs = qs.filter(
                Q(descricao__icontains=busca) | Q(descricao_curta__icontains=busca)
                | Q(codigo__icontains=busca) | Q(codigo_barras__icontains=busca)
                | Q(id_externo__icontains=busca)
            )
        ativo = self.request.query_params.get('ativo', 'true').lower()
        if ativo in {'true', '1', 'sim'}:
            qs = qs.filter(ativo=True)
        elif ativo in {'false', '0', 'nao', 'não'}:
            qs = qs.filter(ativo=False)
        return qs.order_by('id')

    def get(self, request):
        pagina = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(self.get_serializer(pagina, many=True).data)


class ProdutoDetalheApiView(ProdutosApiView):
    pagination_class = None

    def get(self, request, pk):
        produto = self.get_queryset().filter(pk=pk).first()
        if not produto:
            raise exceptions.NotFound('Produto não encontrado neste escopo.')
        return Response(self.get_serializer(produto).data)


class ClientesApiView(BaseIntegracaoView, GenericAPIView):
    escopo_necessario = 'clientes:ler'
    serializer_class = ClienteSerializer
    pagination_class = PaginacaoIntegracao

    def get_queryset(self):
        filiais_ids = self.filiais_ids()
        qs = (
            Cliente.objects.using(self.alias)
            .select_related('filial')
            .filter(
                Q(filial_id__in=filiais_ids)
                | Q(filiais_vinculo__filial_id__in=filiais_ids, filiais_vinculo__ativo=True)
            )
            .distinct()
        )
        qs = self.filtrar_atualizacao(qs)
        busca = self.request.query_params.get('busca', '').strip()
        if busca:
            qs = qs.filter(
                Q(razao_social__icontains=busca) | Q(nome_fantasia__icontains=busca)
                | Q(cpf_cnpj__icontains=busca) | Q(email__icontains=busca)
            )
        return qs.order_by('id')

    def get(self, request):
        pagina = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(self.get_serializer(pagina, many=True).data)


class ClienteDetalheApiView(ClientesApiView):
    pagination_class = None

    def get(self, request, pk):
        cliente = self.get_queryset().filter(pk=pk).first()
        if not cliente:
            raise exceptions.NotFound('Cliente não encontrado neste escopo.')
        return Response(self.get_serializer(cliente).data)


class EstoqueApiView(BaseIntegracaoView, GenericAPIView):
    escopo_necessario = 'estoque:ler'
    serializer_class = EstoqueSerializer
    pagination_class = PaginacaoIntegracao

    def get_queryset(self):
        qs = (
            Estoque.objects.using(self.alias)
            .select_related('filial', 'produto', 'deposito')
        )
        qs = self.filtrar_filial(qs)
        qs = self.filtrar_atualizacao(qs)
        busca = self.request.query_params.get('busca', '').strip()
        if busca:
            qs = qs.filter(
                Q(produto__descricao__icontains=busca)
                | Q(produto__codigo__icontains=busca)
                | Q(produto__codigo_barras__icontains=busca)
            )
        produto_id = self.request.query_params.get('produto_id', '').strip()
        if produto_id:
            qs = qs.filter(produto_id=produto_id)
        return qs.order_by('id')

    def get(self, request):
        pagina = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(self.get_serializer(pagina, many=True).data)


class ProdutosModaApiView(BaseIntegracaoView, GenericAPIView):
    escopo_necessario = 'moda:ler'
    serializer_class = ProdutoModaSerializer
    pagination_class = PaginacaoIntegracao

    def get_queryset(self):
        qs = (
            ProdutoModa.all_objects.using(self.alias)
            .select_related('filial', 'tecido', 'grade')
            .prefetch_related(
                'variantes__produto_cor__cor', 'variantes__tamanho',
            )
        )
        qs = self.filtrar_filial(qs)
        qs = self.filtrar_atualizacao(qs)
        busca = self.request.query_params.get('busca', '').strip()
        if busca:
            qs = qs.filter(
                Q(nome__icontains=busca) | Q(codigo__icontains=busca)
                | Q(referencia__icontains=busca)
            )
        ativo = self.request.query_params.get('ativo', '').lower()
        if ativo in {'true', '1', 'sim'}:
            qs = qs.filter(status=ProdutoModa.Status.ATIVO)
        elif ativo in {'false', '0', 'nao', 'não'}:
            qs = qs.exclude(status=ProdutoModa.Status.ATIVO)
        return qs.order_by('id')

    def get(self, request):
        pagina = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(self.get_serializer(pagina, many=True).data)


class ProdutoModaDetalheApiView(ProdutosModaApiView):
    pagination_class = None

    def get(self, request, pk):
        produto = self.get_queryset().filter(pk=pk).first()
        if not produto:
            raise exceptions.NotFound('Produto de confecção não encontrado neste escopo.')
        return Response(self.get_serializer(produto).data)


class VariantesModaApiView(BaseIntegracaoView, GenericAPIView):
    escopo_necessario = 'moda:ler'
    serializer_class = VarianteModaSerializer
    pagination_class = PaginacaoIntegracao

    def get_queryset(self):
        qs = (
            Variante.objects.using(self.alias)
            .select_related(
                'produto__filial', 'produto_cor__cor', 'tamanho',
            )
        )
        filial_cnpj = self.request.query_params.get('filial_cnpj', '').strip()
        filiais = self.filiais_permitidas()
        if filial_cnpj:
            filiais = filiais.filter(cnpj=filial_cnpj)
        qs = qs.filter(produto__filial_id__in=filiais.values('pk'))
        busca = self.request.query_params.get('busca', '').strip()
        if busca:
            qs = qs.filter(
                Q(sku__icontains=busca) | Q(codigo_barras__icontains=busca)
                | Q(produto__nome__icontains=busca) | Q(produto__codigo__icontains=busca)
            )
        ativo = self.request.query_params.get('ativo', '').lower()
        if ativo in {'true', '1', 'sim'}:
            qs = qs.filter(ativo=True)
        elif ativo in {'false', '0', 'nao', 'não'}:
            qs = qs.filter(ativo=False)
        return qs.order_by('id')

    def get(self, request):
        pagina = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(self.get_serializer(pagina, many=True).data)


class OrdensProducaoApiView(BaseIntegracaoView, GenericAPIView):
    escopo_necessario = 'ops:ler'
    serializer_class = OrdemProducaoSerializer
    pagination_class = PaginacaoIntegracao

    def get_queryset(self):
        qs = (
            OrdemProducao.all_objects.using(self.alias)
            .select_related(
                'filial', 'pedido__cliente', 'item__produto', 'item__cor',
                'item__tecido', 'item__grade_tamanho',
            )
        )
        qs = self.filtrar_filial(qs)
        qs = self.filtrar_atualizacao(qs)
        busca = self.request.query_params.get('busca', '').strip()
        if busca:
            qs = qs.filter(
                Q(numero__icontains=busca) | Q(pedido__numero__icontains=busca)
                | Q(pedido__cliente__razao_social__icontains=busca)
                | Q(pedido__cliente__nome_fantasia__icontains=busca)
                | Q(item__descricao__icontains=busca) | Q(item__produto__nome__icontains=busca)
            )
        status_filtro = self.request.query_params.get('status', '').strip()
        if status_filtro:
            qs = qs.filter(status=status_filtro)
        return qs.order_by('-ano', '-sequencial')

    def get(self, request):
        pagina = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(self.get_serializer(pagina, many=True).data)


class OrdemProducaoDetalheApiView(OrdensProducaoApiView):
    serializer_class = OrdemProducaoDetalheSerializer
    pagination_class = None

    def get(self, request, pk):
        ordem = (
            self.get_queryset()
            .prefetch_related(
                'item__grade__tamanho',
                'item__individuais__tamanho',
                'item__individuais__tamanho_calcao',
            )
            .filter(pk=pk)
            .first()
        )
        if not ordem:
            raise exceptions.NotFound('Ordem de produção não encontrada neste escopo.')
        return Response(self.get_serializer(ordem).data)
