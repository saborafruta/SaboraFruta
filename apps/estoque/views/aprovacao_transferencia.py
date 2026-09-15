"""Fase 20: aprovação de transferência por alçada de valor."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.services.request_scope import empresa_operacional, usuario_operacional
from apps.estoque.models import SolicitacaoTransferencia
from apps.estoque.services.aprovacao_transferencia import (
    aprovar_solicitacao, rejeitar_solicitacao, solicitar_transferencia,
    usuario_pode_executar_direto, valor_estimado_transferencia,
)
from apps.estoque.views.permissoes import permissoes_estoque
from apps.core.models import Filial
from apps.produtos.models import Produto


def _decimal(valor, padrao=None):
    try:
        return Decimal(str(valor).replace(",", "."))
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        return padrao


class TransferenciaGateView(PermissaoRequiredMixin, View):
    """
    Ponto de entrada de toda transferência sugerida pela equalização
    (links "Criar transferência"/"Aplicar transferência"). Dentro da
    alçada do usuário, so' repassa pro formulário real de criação, sem
    mudar nada do fluxo existente. Acima da alçada, pede confirmação e
    cria uma solicitação pendente em vez de ir direto pro formulário.
    """

    permissao_modulo = "estoque"
    permissao_acao = "criar"
    template_name = "estoque/aprovacao_transferencia/confirmar_solicitacao.html"

    def _dados(self, request):
        # A origem e' sempre a filial ativa de quem esta' criando -- o
        # mesmo padrao do formulario real de transferencia (que nunca
        # pede origem por querystring, so' opera na filial logada). O
        # "origem=equilibrio/simulador" na querystring e' so' uma tag de
        # onde veio o clique, nao um id de filial.
        empresa = empresa_operacional(request)
        produto = get_object_or_404(Produto.objects.for_empresa(empresa), pk=request.GET.get("produto") or request.POST.get("produto"))
        origem = request.filial_ativa
        destino = get_object_or_404(Filial, pk=request.GET.get("destino") or request.POST.get("destino"), empresa=empresa)
        quantidade = _decimal(request.GET.get("quantidade") or request.POST.get("quantidade"))
        return produto, origem, destino, quantidade

    def get(self, request):
        produto, origem, destino, quantidade = self._dados(request)
        if not quantidade or quantidade <= 0:
            messages.error(request, "Quantidade inválida.")
            return redirect("estoque:equilibrio-estoque")

        usuario = usuario_operacional(request, obrigatorio=True)
        valor = valor_estimado_transferencia(produto=produto, quantidade=quantidade)
        if usuario_pode_executar_direto(usuario, valor):
            origem_tag = request.GET.get("origem", "equilibrio")
            url = reverse("estoque:transferencia-lojas-create")
            return redirect(f"{url}?destino={destino.pk}&produto={produto.pk}&quantidade={quantidade}&origem={origem_tag}")

        return render(request, self.template_name, {
            "title": "Confirmar solicitação de aprovação",
            "produto": produto, "origem": origem, "destino": destino,
            "quantidade": quantidade, "valor_estimado": valor,
        })

    def post(self, request):
        produto, origem, destino, quantidade = self._dados(request)
        motivo = (request.POST.get("motivo") or "").strip()
        try:
            solicitacao = solicitar_transferencia(
                produto=produto, filial_origem=origem, filial_destino=destino,
                quantidade=quantidade, motivo=motivo,
                solicitante=usuario_operacional(request, obrigatorio=True),
            )
        except DomainError as exc:
            messages.error(request, str(exc))
            return redirect("estoque:equilibrio-estoque")

        messages.success(request, f"Solicitação #{solicitacao.pk} enviada para aprovação.")
        return redirect("estoque:solicitacao-transferencia-list")


class SolicitacaoTransferenciaListView(PermissaoRequiredMixin, View):
    permissao_modulo = "estoque"
    permissao_acao = "ver"
    template_name = "estoque/aprovacao_transferencia/list.html"

    def get(self, request):
        empresa = empresa_operacional(request)
        solicitacoes = (
            SolicitacaoTransferencia.objects.filter(filial_origem__empresa=empresa)
            .select_related("produto", "filial_origem", "filial_destino", "solicitante", "aprovador")
            .order_by("-created_at")
        )
        return render(request, self.template_name, {
            "title": "Solicitações de transferência",
            "solicitacoes": solicitacoes,
            "usuario_pode_decidir": permissoes_estoque(request)["pode_aprovar"],
        })


class SolicitacaoTransferenciaDecidirView(PermissaoRequiredMixin, View):
    permissao_modulo = "estoque"
    permissao_acao = "aprovar"

    def post(self, request, pk):
        acao = request.POST.get("acao")
        observacao = request.POST.get("observacao", "")
        aprovador = usuario_operacional(request, obrigatorio=True)
        try:
            if acao == "aprovar":
                aprovar_solicitacao(solicitacao_id=pk, aprovador=aprovador, observacao=observacao)
                messages.success(request, "Solicitação aprovada e transferência criada.")
            elif acao == "rejeitar":
                rejeitar_solicitacao(solicitacao_id=pk, aprovador=aprovador, observacao=observacao)
                messages.success(request, "Solicitação rejeitada.")
            else:
                messages.error(request, "Ação inválida.")
        except DomainError as exc:
            messages.error(request, str(exc))
        return redirect("estoque:solicitacao-transferencia-list")
