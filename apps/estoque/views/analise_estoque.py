from django.core.paginator import Paginator
from django.shortcuts import render
from django.views import View

from apps.core.models import Filial
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.estoque.services.analise_estoque import classificar_abc_giro, produtos_em_excesso
from apps.estoque.views.permissoes import permissoes_estoque


def _inteiro_opcao(valor, permitidos, padrao):
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return padrao
    return numero if numero in permitidos else padrao


def _filial_opcional(request, empresa):
    bruto = request.GET.get("filial")
    if not bruto:
        return None
    return Filial.objects.filter(pk=bruto, empresa=empresa, ativo=True).first()


def _filiais_da_empresa(empresa):
    return list(
        Filial.objects.filter(empresa=empresa, ativo=True)
        .order_by("-is_matriz", "nome_fantasia", "razao_social")
    )


class CurvaAbcGiroView(PermissaoRequiredMixin, View):
    """Classificação ABC por volume vendido (giro), não por receita."""

    permissao_modulo = "estoque"
    permissao_acao = "ver"
    template_name = "estoque/analise/curva_abc.html"

    def get(self, request):
        empresa = request.user.empresa
        dias_analise = _inteiro_opcao(request.GET.get("dias_analise"), {30, 60, 90, 180, 365}, 90)
        classe_filtro = (request.GET.get("classe") or "").strip().upper()
        if classe_filtro not in {"A", "B", "C"}:
            classe_filtro = ""
        filial = _filial_opcional(request, empresa)

        resultado = classificar_abc_giro(empresa=empresa, filial=filial, dias_analise=dias_analise)
        itens = resultado["itens"]
        if classe_filtro:
            itens = [item for item in itens if item["classe"] == classe_filtro]

        pagina = Paginator(itens, 50).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "title": "Curva ABC por giro",
            "pagina": pagina,
            "resumo": resultado["resumo"],
            "total_produtos": len(resultado["itens"]),
            "dias_analise": dias_analise,
            "classe_filtro": classe_filtro,
            "filiais": _filiais_da_empresa(empresa),
            "filial_id": filial.pk if filial else None,
            "permissoes_estoque": permissoes_estoque(request),
        })


class ProdutosExcessoView(PermissaoRequiredMixin, View):
    """Produtos com saldo acima do estoque máximo -- capital parado."""

    permissao_modulo = "estoque"
    permissao_acao = "ver"
    template_name = "estoque/analise/excesso.html"

    def get(self, request):
        empresa = request.user.empresa
        filial = _filial_opcional(request, empresa)

        itens = produtos_em_excesso(empresa=empresa, filial=filial)
        total_excedente = sum((item["excedente"] for item in itens), 0)

        pagina = Paginator(itens, 50).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "title": "Produtos em excesso",
            "pagina": pagina,
            "total_produtos": len(itens),
            "total_excedente": total_excedente,
            "filiais": _filiais_da_empresa(empresa),
            "filial_id": filial.pk if filial else None,
            "permissoes_estoque": permissoes_estoque(request),
        })
