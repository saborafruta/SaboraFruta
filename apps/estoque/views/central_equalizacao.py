"""Central de equalização: tabela única com todos os filtros da equalização de estoque."""
from decimal import Decimal, InvalidOperation

from django.core.paginator import Paginator
from django.shortcuts import render
from django.views import View

from apps.core.models import Filial
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.estoque.models import LoteProduto
from apps.estoque.services.equilibrio_estoque import calcular_equilibrio
from apps.estoque.services.posicoes_estoque import calcular_posicoes
from apps.estoque.views.permissoes import permissoes_estoque
from apps.produtos.models import CategoriaProduto, MarcaProduto
from apps.cadastros.models import Fornecedor


def _inteiro_opcao(valor, permitidos, padrao):
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return padrao
    return numero if numero in permitidos else padrao


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
        return Decimal(str(valor).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


CLASSES_STATUS = {"ruptura", "critico", "baixo", "normal", "alto", "excesso"}
CLASSES_ABC = {"A", "B", "C"}


class CentralEqualizacaoView(PermissaoRequiredMixin, View):
    permissao_modulo = "estoque"
    permissao_acao = "ver"
    template_name = "estoque/central_equalizacao/central.html"

    def get(self, request):
        empresa = request.user.empresa
        dias_analise = _inteiro_opcao(request.GET.get("dias_analise"), {7, 15, 30, 60, 90}, 30)
        dias_cobertura = _inteiro_opcao(request.GET.get("dias_cobertura"), {7, 14, 21, 30, 45}, 14)

        busca = (request.GET.get("produto") or "").strip()[:150]
        categoria_id = _id_opcional(request.GET.get("categoria"))
        marca_id = _id_opcional(request.GET.get("marca"))
        fornecedor_id = _id_opcional(request.GET.get("fornecedor"))
        curva_abc = (request.GET.get("curva_abc") or "").strip().upper()
        if curva_abc not in CLASSES_ABC:
            curva_abc = ""
        status_estoque = (request.GET.get("status") or "").strip().lower()
        if status_estoque not in CLASSES_STATUS:
            status_estoque = ""
        lote_busca = (request.GET.get("lote") or "").strip()[:60]
        somente_perto_vencer = request.GET.get("validade") == "perto_vencer"
        valor_minimo = _decimal_opcional(request.GET.get("valor_minimo"))
        valor_maximo = _decimal_opcional(request.GET.get("valor_maximo"))
        filial_origem_id = _id_opcional(request.GET.get("origem"))
        filial_destino_id = _id_opcional(request.GET.get("destino"))

        posicoes = calcular_posicoes(empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura)

        if filial_origem_id or filial_destino_id:
            filiais_alvo = {fid for fid in (filial_origem_id, filial_destino_id) if fid}
            posicoes = [p for p in posicoes if p["filial"].pk in filiais_alvo]
        if busca:
            termo = busca.casefold()
            posicoes = [
                p for p in posicoes
                if termo in p["produto"].descricao.casefold()
                or termo in (p["produto"].codigo or "").casefold()
                or termo in (p["produto"].codigo_barras or "")
            ]
        if categoria_id:
            posicoes = [p for p in posicoes if p["produto"].categoria_id == categoria_id]
        if marca_id:
            posicoes = [p for p in posicoes if p["produto"].marca_id == marca_id]
        if fornecedor_id:
            posicoes = [p for p in posicoes if p["produto"].fornecedor_id == fornecedor_id]
        if curva_abc:
            posicoes = [p for p in posicoes if p["classe_abc"] == curva_abc]
        if status_estoque:
            posicoes = [p for p in posicoes if p["classe"] == status_estoque]
        if somente_perto_vencer:
            posicoes = [p for p in posicoes if p["lote_dias_vencer"] is not None]
        if lote_busca:
            produto_ids_com_lote = set(
                LoteProduto.objects.filter(
                    produto_id__in={p["produto"].pk for p in posicoes},
                    numero_lote__icontains=lote_busca,
                ).values_list("produto_id", flat=True)
            )
            posicoes = [p for p in posicoes if p["produto"].pk in produto_ids_com_lote]
        if valor_minimo is not None:
            posicoes = [p for p in posicoes if p["valor"] >= valor_minimo]
        if valor_maximo is not None:
            posicoes = [p for p in posicoes if p["valor"] <= valor_maximo]

        posicoes.sort(key=lambda p: (p["produto"].descricao, p["filial"].nome_fantasia or ""))

        sugestoes = []
        if filial_origem_id or filial_destino_id:
            resultado = calcular_equilibrio(
                empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura,
                busca=busca, filial_origem_id=filial_origem_id, filial_destino_id=filial_destino_id,
            )
            sugestoes = resultado["sugestoes"]

        pagina = Paginator(posicoes, 50).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "title": "Central de equalização",
            "pagina": pagina,
            "total_posicoes": len(posicoes),
            "sugestoes": sugestoes,
            "filiais": Filial.objects.filter(empresa=empresa, ativo=True).order_by("-is_matriz", "nome_fantasia", "razao_social"),
            "categorias": CategoriaProduto.objects.filter(empresa=empresa, ativo=True).order_by("nome"),
            "marcas": MarcaProduto.objects.for_empresa(empresa).order_by("nome"),
            "fornecedores": Fornecedor.objects.for_empresa(empresa).order_by("razao_social"),
            "filtros": {
                "produto": busca, "categoria": categoria_id, "marca": marca_id,
                "fornecedor": fornecedor_id, "curva_abc": curva_abc, "status": status_estoque,
                "lote": lote_busca, "validade": "perto_vencer" if somente_perto_vencer else "",
                "valor_minimo": request.GET.get("valor_minimo", ""), "valor_maximo": request.GET.get("valor_maximo", ""),
                "origem": filial_origem_id, "destino": filial_destino_id,
            },
            "dias_analise": dias_analise,
            "dias_cobertura": dias_cobertura,
            "permissoes_estoque": permissoes_estoque(request),
        })
