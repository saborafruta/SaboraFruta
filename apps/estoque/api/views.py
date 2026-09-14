"""
API REST do módulo de equalização de estoque (Fase 31).

Autenticação: sessão do próprio ERP (a mesma usada pelas telas HTML) --
ver a nota em `apps/estoque/api/urls.py` sobre por que não é JWT ainda
nesta arquitetura multi-tenant. RBAC via `TemPermissaoEstoque`, que
reaproveita `usuario.tem_permissao('estoque', acao)`, o mesmo já usado
em toda tela do ERP.

Cada view aqui é uma casca fina sobre os services já testados
(`equilibrio_estoque`, `dashboard_equalizacao`, `simulador_transferencia`,
`aprovacao_transferencia`, `indicadores_performance`) -- nenhuma regra
de negócio nova mora aqui.
"""
from django.shortcuts import get_object_or_404
from rest_framework.authentication import SessionAuthentication
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Filial
from apps.core.services.exceptions import DomainError
from apps.core.services.request_scope import empresa_operacional, usuario_operacional
from apps.estoque.models import SolicitacaoTransferencia, SugestaoEqualizacaoSnapshot
from apps.estoque.services.aprovacao_transferencia import (
    aprovar_solicitacao, criar_ou_solicitar_transferencia, rejeitar_solicitacao,
)
from apps.estoque.services.dashboard_equalizacao import montar_dashboard
from apps.estoque.services.equilibrio_estoque import calcular_equilibrio
from apps.estoque.services.indicadores_performance import calcular_indicadores
from apps.estoque.services.simulador_transferencia import simular_transferencia
from apps.produtos.models import Produto

from .permissions import TemPermissaoEstoque
from .serializers import (
    AnaliseFiltroSerializer, AprovarRejeitarInputSerializer, DashboardSerializer,
    IndicadoresSerializer, SimulacaoResultadoSerializer, SimularTransferenciaInputSerializer,
    SnapshotHistoricoSerializer, SolicitacaoTransferenciaSerializer, SugestaoSerializer,
    TransferirInputSerializer,
)


class PaginacaoEqualizacao(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


class BaseEqualizacaoAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [TemPermissaoEstoque]
    permissao_acao = "ver"


def _filtros_analise(request_data) -> dict:
    filtro = AnaliseFiltroSerializer(data=request_data)
    filtro.is_valid(raise_exception=True)
    return filtro.validated_data


class EqualizacaoStatusView(BaseEqualizacaoAPIView):
    """GET /api/estoque/equalizacao/ -- visão geral rápida do estado da equalização."""

    def get(self, request):
        empresa = empresa_operacional(request)
        dados = _filtros_analise(request.query_params)
        resultado = calcular_equilibrio(
            empresa=empresa, dias_analise=dados["dias_analise"], dias_cobertura=dados["dias_cobertura"],
        )
        filiais = Filial.objects.filter(empresa=empresa, ativo=True)
        return Response({
            "produtos_analisados": resultado["produtos_analisados"],
            "total_sugestoes": len(resultado["sugestoes"]),
            "total_filiais": filiais.count(),
            "dias_analise": dados["dias_analise"],
            "dias_cobertura": dados["dias_cobertura"],
        })


class EqualizacaoRecomendacoesView(BaseEqualizacaoAPIView):
    """GET /api/estoque/equalizacao/recomendacoes/ -- lista paginada de sugestões de transferência."""

    def get(self, request):
        empresa = empresa_operacional(request)
        dados = _filtros_analise(request.query_params)
        resultado = calcular_equilibrio(
            empresa=empresa, dias_analise=dados["dias_analise"], dias_cobertura=dados["dias_cobertura"],
            busca=dados["busca"], filial_origem_id=dados["origem"], filial_destino_id=dados["destino"],
        )
        paginador = PaginacaoEqualizacao()
        pagina = paginador.paginate_queryset(resultado["sugestoes"], request, view=self)
        serializer = SugestaoSerializer(pagina, many=True)
        return paginador.get_paginated_response(serializer.data)


class EqualizacaoAnalisarView(BaseEqualizacaoAPIView):
    """POST /api/estoque/equalizacao/analisar/ -- mesmo cálculo de /recomendacoes/, mas com filtros no corpo (útil para filtros complexos vindos de um client)."""

    def post(self, request):
        empresa = empresa_operacional(request)
        dados = _filtros_analise(request.data)
        resultado = calcular_equilibrio(
            empresa=empresa, dias_analise=dados["dias_analise"], dias_cobertura=dados["dias_cobertura"],
            busca=dados["busca"], filial_origem_id=dados["origem"], filial_destino_id=dados["destino"],
        )
        return Response({
            "produtos_analisados": resultado["produtos_analisados"],
            "total_sugestoes": len(resultado["sugestoes"]),
            "sugestoes": SugestaoSerializer(resultado["sugestoes"], many=True).data,
        })


class EqualizacaoDashboardView(BaseEqualizacaoAPIView):
    """GET /api/estoque/equalizacao/dashboard/"""

    def get(self, request):
        empresa = empresa_operacional(request)
        dados = _filtros_analise(request.query_params)
        cards = montar_dashboard(empresa=empresa, dias_analise=dados["dias_analise"], dias_cobertura=dados["dias_cobertura"])
        return Response(DashboardSerializer(cards).data)


class EqualizacaoIndicadoresView(BaseEqualizacaoAPIView):
    """GET /api/estoque/equalizacao/indicadores/"""

    def get(self, request):
        empresa = empresa_operacional(request)
        dados = _filtros_analise(request.query_params)
        dias_historico = int(request.query_params.get("dias_historico", 30))
        resultado = calcular_indicadores(
            empresa=empresa, dias_analise=dados["dias_analise"], dias_cobertura=dados["dias_cobertura"],
            dias_historico=dias_historico,
        )
        return Response(IndicadoresSerializer(resultado).data)


class EqualizacaoHistoricoView(BaseEqualizacaoAPIView):
    """GET /api/estoque/equalizacao/historico/ -- snapshots gerados pela rotina de automação (Fase 29)."""

    def get(self, request):
        empresa = empresa_operacional(request)
        queryset = (
            SugestaoEqualizacaoSnapshot.objects.filter(empresa=empresa)
            .select_related("produto", "filial_origem", "filial_destino")
            .order_by("-created_at")
        )
        paginador = PaginacaoEqualizacao()
        pagina = paginador.paginate_queryset(queryset, request, view=self)
        return paginador.get_paginated_response(SnapshotHistoricoSerializer(pagina, many=True).data)


class EqualizacaoSimularView(BaseEqualizacaoAPIView):
    """POST /api/estoque/equalizacao/simular/ -- prévia de antes/depois, nunca move estoque."""

    def post(self, request):
        entrada = SimularTransferenciaInputSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        empresa = empresa_operacional(request)
        get_object_or_404(Produto.objects.for_empresa(empresa), pk=dados["produto_id"])
        get_object_or_404(Filial, pk=dados["origem_id"], empresa=empresa)
        get_object_or_404(Filial, pk=dados["destino_id"], empresa=empresa)

        resultado = simular_transferencia(
            empresa=empresa, produto_id=dados["produto_id"], origem_id=dados["origem_id"],
            destino_id=dados["destino_id"], quantidade=dados["quantidade"],
            dias_analise=dados["dias_analise"], dias_cobertura=dados["dias_cobertura"],
        )
        if resultado is None:
            return Response({"detail": "Produto não vinculado a uma das filiais informadas."}, status=400)
        return Response(SimulacaoResultadoSerializer(resultado).data)


class EqualizacaoTransferirView(BaseEqualizacaoAPIView):
    """
    POST /api/estoque/equalizacao/transferir/ -- transferência simples
    (sem NF-e/MDF-e) a partir da filial ativa de quem chama. Dentro da
    alçada do perfil, executa na hora; acima, vira solicitação pendente
    (ver /aprovar/ e /rejeitar/). Para transferência com nota fiscal,
    use o formulário completo do ERP.
    """

    permissao_acao = "criar"

    def post(self, request):
        entrada = TransferirInputSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        empresa = empresa_operacional(request)
        produto = get_object_or_404(Produto.objects.for_empresa(empresa), pk=dados["produto_id"])
        destino = get_object_or_404(Filial, pk=dados["destino_id"], empresa=empresa)
        origem = request.filial_ativa
        usuario = usuario_operacional(request, obrigatorio=True)

        try:
            resultado = criar_ou_solicitar_transferencia(
                produto=produto, filial_origem=origem, filial_destino=destino,
                quantidade=dados["quantidade"], motivo=dados["motivo"], usuario=usuario, request=request,
            )
        except DomainError as exc:
            return Response({"detail": str(exc)}, status=400)

        if resultado["executada"]:
            return Response({
                "executada": True, "documento_numero": resultado["documento_numero"], "solicitacao": None,
            }, status=201)
        return Response({
            "executada": False, "documento_numero": "",
            "solicitacao": SolicitacaoTransferenciaSerializer(resultado["solicitacao"]).data,
        }, status=202)


class EqualizacaoAprovarView(BaseEqualizacaoAPIView):
    """POST /api/estoque/equalizacao/aprovar/"""

    permissao_acao = "aprovar"

    def post(self, request):
        entrada = AprovarRejeitarInputSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        aprovador = usuario_operacional(request, obrigatorio=True)
        try:
            solicitacao = aprovar_solicitacao(
                solicitacao_id=dados["solicitacao_id"], aprovador=aprovador,
                observacao=dados["observacao"], request=request,
            )
        except SolicitacaoTransferencia.DoesNotExist:
            return Response({"detail": "Solicitação não encontrada."}, status=404)
        except DomainError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(SolicitacaoTransferenciaSerializer(solicitacao).data)


class EqualizacaoRejeitarView(BaseEqualizacaoAPIView):
    """POST /api/estoque/equalizacao/rejeitar/"""

    permissao_acao = "aprovar"

    def post(self, request):
        entrada = AprovarRejeitarInputSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        aprovador = usuario_operacional(request, obrigatorio=True)
        try:
            solicitacao = rejeitar_solicitacao(
                solicitacao_id=dados["solicitacao_id"], aprovador=aprovador,
                observacao=dados["observacao"], request=request,
            )
        except SolicitacaoTransferencia.DoesNotExist:
            return Response({"detail": "Solicitação não encontrada."}, status=404)
        except DomainError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(SolicitacaoTransferenciaSerializer(solicitacao).data)
