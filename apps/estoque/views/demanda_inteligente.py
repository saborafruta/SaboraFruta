"""Fase 16: inteligência de demanda -- média ponderada, dia da semana e sazonalidade."""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views import View

from apps.core.models import Filial
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.services.request_scope import empresa_operacional
from apps.estoque.forms import ConfiguracaoDemandaPonderadaForm
from apps.estoque.models import ConfiguracaoDemandaPonderada
from apps.estoque.services.demanda_inteligente import (
    calcular_demanda_ponderada, calcular_indice_dia_semana, calcular_sazonalidade, carregar_pesos,
)
from apps.estoque.views.permissoes import permissoes_estoque
from apps.produtos.models import Produto


def _id_opcional(valor):
    try:
        numero = int(valor)
        return numero if numero > 0 else None
    except (TypeError, ValueError):
        return None


class ConfiguracaoDemandaPonderadaView(PermissaoRequiredMixin, View):
    permissao_modulo = "estoque"
    permissao_acao = "ver"
    template_name = "estoque/demanda_inteligente/configuracao.html"

    def get(self, request):
        empresa = empresa_operacional(request)
        config, _ = ConfiguracaoDemandaPonderada.objects.get_or_create(empresa=empresa)
        return render(request, self.template_name, {
            "title": "Pesos da demanda ponderada",
            "form": ConfiguracaoDemandaPonderadaForm(instance=config),
            "permissoes_estoque": permissoes_estoque(request),
        })

    def post(self, request):
        if not request.user.tem_permissao("estoque", "editar"):
            messages.error(request, "Você não tem permissão para esta ação.")
            return redirect("estoque:demanda-inteligente-config")
        empresa = empresa_operacional(request)
        config, _ = ConfiguracaoDemandaPonderada.objects.get_or_create(empresa=empresa)
        form = ConfiguracaoDemandaPonderadaForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            messages.success(request, "Pesos atualizados.")
            return redirect("estoque:demanda-inteligente-config")
        return render(request, self.template_name, {
            "title": "Pesos da demanda ponderada",
            "form": form,
            "permissoes_estoque": permissoes_estoque(request),
        })


class DemandaInteligenteView(PermissaoRequiredMixin, View):
    permissao_modulo = "estoque"
    permissao_acao = "ver"
    template_name = "estoque/demanda_inteligente/analise.html"

    def get(self, request):
        empresa = empresa_operacional(request)
        produto_id = _id_opcional(request.GET.get("produto"))
        filial_id = _id_opcional(request.GET.get("filial"))

        produto_selecionado = None
        resultado = None
        if produto_id:
            produto_selecionado = Produto.objects.for_empresa(empresa).filter(pk=produto_id).first()
        if produto_id and filial_id and produto_selecionado:
            pesos = carregar_pesos(empresa=empresa)
            resultado = {
                "demanda": calcular_demanda_ponderada(produto_id=produto_id, filial_id=filial_id, pesos=pesos),
                "dia_semana": calcular_indice_dia_semana(produto_id=produto_id, filial_id=filial_id),
                "sazonalidade": calcular_sazonalidade(produto_id=produto_id, filial_id=filial_id),
            }

        return render(request, self.template_name, {
            "title": "Inteligência de demanda",
            "filiais": Filial.objects.filter(empresa=empresa, ativo=True).order_by("-is_matriz", "nome_fantasia", "razao_social"),
            "produto_selecionado": produto_selecionado,
            "filial_id": filial_id,
            "resultado": resultado,
            "permissoes_estoque": permissoes_estoque(request),
        })
