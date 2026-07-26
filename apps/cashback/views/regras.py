"""CRUD das regras de cashback por produto/categoria/filial/empresa."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.models import Filial
from apps.produtos.models import CategoriaProduto, Produto

from ..models import (
    RegraCashbackCategoria,
    RegraCashbackEmpresa,
    RegraCashbackFilial,
    RegraCashbackProduto,
)

_MODELOS = {
    "produto": (RegraCashbackProduto, "produto"),
    "categoria": (RegraCashbackCategoria, "categoria"),
    "filial": (RegraCashbackFilial, "filial"),
    "empresa": (RegraCashbackEmpresa, "empresa"),
}


class RegrasCashbackView(PermissaoRequiredMixin, View):
    permissao_modulo = "cashback"
    permissao_acao = "ver"
    template_name = "cashback/regras.html"

    def get(self, request):
        filial = request.filial_ativa
        nivel = request.GET.get("nivel", "produto")
        if nivel not in _MODELOS:
            nivel = "produto"

        regras_produto = RegraCashbackProduto.objects.select_related("produto").order_by("produto__descricao")
        regras_categoria = RegraCashbackCategoria.objects.select_related("categoria").order_by("categoria__nome")
        regras_filial = RegraCashbackFilial.objects.select_related("filial").filter(
            filial__empresa=filial.empresa,
        ).order_by("filial__nome_fantasia")
        regras_empresa = RegraCashbackEmpresa.objects.select_related("empresa").filter(
            empresa=filial.empresa,
        )

        categorias = []
        if nivel == "categoria":
            categorias = sorted(
                CategoriaProduto.objects.for_filial(filial).exclude(regra_cashback__isnull=False),
                key=lambda c: c.full_path(),
            )

        return render(request, self.template_name, {
            "title": "Regras de Cashback",
            "nivel": nivel,
            "empresa_id": filial.empresa_id,
            "filiais": Filial.objects.filter(empresa=filial.empresa),
            "categorias": categorias,
            "regras_produto": regras_produto,
            "regras_categoria": regras_categoria,
            "regras_filial": regras_filial,
            "regras_empresa": regras_empresa,
        })

    def post(self, request):
        filial = request.filial_ativa
        nivel = request.POST.get("nivel")
        if nivel not in _MODELOS:
            messages.error(request, "Nível de regra inválido.")
            return redirect(reverse("cashback:regras"))

        modelo, campo_alvo = _MODELOS[nivel]

        try:
            percentual = Decimal(request.POST.get("percentual", "0").replace(",", "."))
        except InvalidOperation:
            messages.error(request, "Percentual inválido.")
            return redirect(f"{reverse('cashback:regras')}?nivel={nivel}")

        valor_minimo_raw = request.POST.get("valor_minimo_gerar", "").strip()
        valor_minimo_gerar = None
        if valor_minimo_raw:
            try:
                valor_minimo_gerar = Decimal(valor_minimo_raw.replace(",", "."))
            except InvalidOperation:
                messages.error(request, "Valor mínimo inválido.")
                return redirect(f"{reverse('cashback:regras')}?nivel={nivel}")

        valor_fixo_raw = request.POST.get("valor_fixo_unidade", "").strip()
        valor_fixo_unidade = None
        if valor_fixo_raw:
            try:
                valor_fixo_unidade = Decimal(valor_fixo_raw.replace(",", "."))
            except InvalidOperation:
                messages.error(request, "Valor fixo por unidade inválido.")
                return redirect(f"{reverse('cashback:regras')}?nivel={nivel}")

        gera_cashback = request.POST.get("gera_cashback", "on") == "on"
        ativo = request.POST.get("ativo", "on") == "on"

        alvo_id = request.POST.get("alvo_id")
        if not alvo_id:
            messages.error(request, "Selecione o alvo da regra.")
            return redirect(f"{reverse('cashback:regras')}?nivel={nivel}")

        dados = {
            "percentual": percentual,
            "valor_fixo_unidade": valor_fixo_unidade,
            "valor_minimo_gerar": valor_minimo_gerar,
            "ativo": ativo,
        }
        if nivel in ("produto", "categoria"):
            dados["gera_cashback"] = gera_cashback

        filtro_alvo = {f"{campo_alvo}_id": alvo_id}
        modelo.objects.update_or_create(**filtro_alvo, defaults=dados)
        messages.success(request, "Regra de cashback salva.")
        return redirect(f"{reverse('cashback:regras')}?nivel={nivel}")


class RegraCashbackBuscaAlvoView(PermissaoRequiredMixin, View):
    """Busca AJAX de produto/categoria para a aba correspondente de Regras de Cashback."""
    permissao_modulo = "cashback"
    permissao_acao = "ver"

    def get(self, request):
        filial = request.filial_ativa
        nivel = request.GET.get("nivel", "produto")
        q = request.GET.get("q", "").strip()
        resultados = []
        if len(q) < 2:
            return JsonResponse({"resultados": resultados})

        if nivel == "produto":
            produtos = (
                Produto.objects.for_filial(filial)
                .filter(Q(descricao__icontains=q) | Q(codigo__icontains=q))
                .exclude(regra_cashback__isnull=False)[:15]
            )
            resultados = [
                {"id": p.pk, "nome": p.descricao, "codigo": p.codigo or ""}
                for p in produtos
            ]
        elif nivel == "categoria":
            categorias = (
                CategoriaProduto.objects.for_filial(filial)
                .filter(nome__icontains=q)
                .exclude(regra_cashback__isnull=False)[:15]
            )
            resultados = [
                {"id": c.pk, "nome": c.full_path()}
                for c in categorias
            ]

        return JsonResponse({"resultados": resultados})


class RegraCashbackDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = "cashback"
    permissao_acao = "editar"

    def post(self, request, nivel, pk):
        if nivel not in _MODELOS:
            messages.error(request, "Nível de regra inválido.")
            return redirect(reverse("cashback:regras"))
        modelo, _campo = _MODELOS[nivel]
        regra = get_object_or_404(modelo, pk=pk)
        regra.delete()
        messages.success(request, "Regra removida.")
        return redirect(f"{reverse('cashback:regras')}?nivel={nivel}")
