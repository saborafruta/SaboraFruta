"""CRUD de campanhas promocionais de cashback."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.models import Filial
from apps.produtos.models import CategoriaProduto, Produto

from ..models import CampanhaCashback


class CampanhaCashbackListView(PermissaoRequiredMixin, View):
    permissao_modulo = "cashback"
    permissao_acao = "ver"

    def get(self, request):
        empresa = request.filial_ativa.empresa
        campanhas = CampanhaCashback.objects.filter(empresa=empresa)
        return render(request, "cashback/campanha_list.html", {
            "title": "Campanhas de Cashback",
            "campanhas": campanhas,
        })


class _CampanhaFormMixin:
    template_name = "cashback/campanha_form.html"

    def _contexto(self, request, campanha=None):
        empresa = request.filial_ativa.empresa
        return {
            "title": "Editar Campanha" if campanha else "Nova Campanha de Cashback",
            "campanha": campanha,
            "produtos": Produto.objects.for_filial(request.filial_ativa)[:500],
            "categorias": CategoriaProduto.objects.for_filial(request.filial_ativa),
            "filiais": Filial.objects.filter(empresa=empresa),
            "cancel_url": reverse("cashback:campanha-list"),
        }

    def _salvar(self, request, campanha):
        empresa = request.filial_ativa.empresa
        try:
            percentual = Decimal(request.POST.get("percentual", "0").replace(",", "."))
            prioridade = int(request.POST.get("prioridade", "0") or 0)
        except (InvalidOperation, ValueError):
            messages.error(request, "Verifique os valores de percentual/prioridade.")
            return None

        nome = request.POST.get("nome", "").strip()
        data_inicio = request.POST.get("data_inicio")
        data_fim = request.POST.get("data_fim")
        if not nome or not data_inicio or not data_fim:
            messages.error(request, "Nome, data início e data fim são obrigatórios.")
            return None

        dias_semana = [int(d) for d in request.POST.getlist("dias_semana")]

        if campanha is None:
            campanha = CampanhaCashback(empresa=empresa)
        campanha.nome = nome
        campanha.descricao = request.POST.get("descricao", "").strip()
        campanha.percentual = percentual
        campanha.data_inicio = data_inicio
        campanha.data_fim = data_fim
        campanha.prioridade = prioridade
        campanha.dias_semana = dias_semana
        campanha.ativo = request.POST.get("ativo", "on") == "on"
        campanha.save()

        campanha.produtos.set(request.POST.getlist("produtos"))
        campanha.categorias.set(request.POST.getlist("categorias"))
        campanha.filiais.set(request.POST.getlist("filiais"))
        return campanha


class CampanhaCashbackCreateView(_CampanhaFormMixin, PermissaoRequiredMixin, View):
    permissao_modulo = "cashback"
    permissao_acao = "criar"

    def get(self, request):
        return render(request, self.template_name, self._contexto(request))

    def post(self, request):
        campanha = self._salvar(request, None)
        if campanha is None:
            return render(request, self.template_name, self._contexto(request))
        messages.success(request, f'Campanha "{campanha.nome}" criada.')
        return redirect(reverse("cashback:campanha-list"))


class CampanhaCashbackUpdateView(_CampanhaFormMixin, PermissaoRequiredMixin, View):
    permissao_modulo = "cashback"
    permissao_acao = "editar"

    def get(self, request, pk):
        campanha = get_object_or_404(CampanhaCashback, pk=pk, empresa=request.filial_ativa.empresa)
        return render(request, self.template_name, self._contexto(request, campanha))

    def post(self, request, pk):
        campanha = get_object_or_404(CampanhaCashback, pk=pk, empresa=request.filial_ativa.empresa)
        campanha = self._salvar(request, campanha)
        if campanha is None:
            return render(request, self.template_name, self._contexto(request, campanha))
        messages.success(request, f'Campanha "{campanha.nome}" atualizada.')
        return redirect(reverse("cashback:campanha-list"))


class CampanhaCashbackToggleView(PermissaoRequiredMixin, View):
    permissao_modulo = "cashback"
    permissao_acao = "editar"

    def post(self, request, pk):
        campanha = get_object_or_404(CampanhaCashback, pk=pk, empresa=request.filial_ativa.empresa)
        campanha.ativo = not campanha.ativo
        campanha.save(update_fields=["ativo", "updated_at"])
        messages.success(request, f'Campanha {"ativada" if campanha.ativo else "desativada"}.')
        return redirect(reverse("cashback:campanha-list"))
