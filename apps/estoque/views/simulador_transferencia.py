from decimal import Decimal, InvalidOperation

from django.shortcuts import render
from django.views import View

from apps.core.models import Filial
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.estoque.services.simulador_transferencia import simular_transferencia
from apps.estoque.views.permissoes import permissoes_estoque
from apps.produtos.models import Produto


def _id_opcional(valor):
    try:
        numero = int(valor)
        return numero if numero > 0 else None
    except (TypeError, ValueError):
        return None


def _decimal_opcional(valor):
    if not valor:
        return None
    try:
        numero = Decimal(str(valor).replace(",", "."))
        return numero if numero > 0 else None
    except (InvalidOperation, ValueError):
        return None


class SimuladorTransferenciaView(PermissaoRequiredMixin, View):
    """Fase 15: prévia de antes/depois de uma transferência manual antes de aplicá-la (Recomendado)."""

    permissao_modulo = "estoque"
    permissao_acao = "ver"
    template_name = "estoque/simulador_transferencia/simulador.html"

    def get(self, request):
        empresa = request.user.empresa
        produto_id = _id_opcional(request.GET.get("produto"))
        origem_id = _id_opcional(request.GET.get("origem"))
        destino_id = _id_opcional(request.GET.get("destino"))
        quantidade = _decimal_opcional(request.GET.get("quantidade"))

        produto_selecionado = None
        if produto_id:
            produto_selecionado = Produto.objects.for_empresa(empresa).filter(pk=produto_id).first()

        simulacao = None
        erro = None
        if produto_id and origem_id and destino_id and quantidade:
            if origem_id == destino_id:
                erro = "Escolha filiais diferentes para origem e destino."
            else:
                simulacao = simular_transferencia(
                    empresa=empresa, produto_id=produto_id, origem_id=origem_id,
                    destino_id=destino_id, quantidade=quantidade,
                )
                if simulacao is None:
                    erro = "Este produto não está vinculado a uma das filiais selecionadas."

        return render(request, self.template_name, {
            "title": "Simulador de transferência",
            "filiais": Filial.objects.filter(empresa=empresa, ativo=True).order_by("-is_matriz", "nome_fantasia", "razao_social"),
            "produto_selecionado": produto_selecionado,
            "origem_id": origem_id,
            "destino_id": destino_id,
            "quantidade": request.GET.get("quantidade", ""),
            "simulacao": simulacao,
            "erro": erro,
            "permissoes_estoque": permissoes_estoque(request),
        })
