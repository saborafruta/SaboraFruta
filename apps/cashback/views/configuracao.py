"""Configuração de cashback (percentual global, mínimos, limite, validade)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin

from ..models import ConfiguracaoCashback


def _filial(request):
    return request.filial_ativa


class ConfiguracaoCashbackView(PermissaoRequiredMixin, View):
    permissao_modulo = "cashback"
    permissao_acao = "ver"
    template_name = "cashback/configuracao.html"

    def _contexto(self, request):
        filial = _filial(request)
        config = ConfiguracaoCashback.objects.filter(filial=filial).first()
        config_global = ConfiguracaoCashback.objects.filter(
            empresa=filial.empresa, filial__isnull=True,
        ).first()
        return {
            "title": "Configuração de Cashback",
            "filial": filial,
            "config": config,
            "config_global": config_global,
        }

    def get(self, request):
        return render(request, self.template_name, self._contexto(request))

    def post(self, request):
        filial = _filial(request)
        escopo = request.POST.get("escopo", "filial")  # 'filial' ou 'global'

        try:
            percentual_global = Decimal(request.POST.get("percentual_global", "0").replace(",", "."))
            valor_minimo_gerar = Decimal(request.POST.get("valor_minimo_gerar", "0").replace(",", "."))
            valor_minimo_usar = Decimal(request.POST.get("valor_minimo_usar", "0").replace(",", "."))
            percentual_maximo_uso_venda = Decimal(
                request.POST.get("percentual_maximo_uso_venda", "100").replace(",", ".")
            )
            dias_validade = int(request.POST.get("dias_validade", "90"))
        except (InvalidOperation, ValueError):
            messages.error(request, "Verifique os valores numéricos informados.")
            return redirect(reverse("cashback:configuracao"))

        modo_estorno_usado = request.POST.get(
            "modo_estorno_usado", ConfiguracaoCashback.ModoEstornoUsado.CONTA_A_RECEBER,
        )
        ativo = request.POST.get("ativo") == "on"

        dados = dict(
            percentual_global=percentual_global,
            valor_minimo_gerar=valor_minimo_gerar,
            valor_minimo_usar=valor_minimo_usar,
            percentual_maximo_uso_venda=percentual_maximo_uso_venda,
            dias_validade=dias_validade,
            modo_estorno_usado=modo_estorno_usado,
            ativo=ativo,
        )

        if escopo == "global":
            ConfiguracaoCashback.objects.update_or_create(
                empresa=filial.empresa, filial=None, defaults=dados,
            )
            messages.success(request, "Configuração global (padrão da empresa) salva.")
        else:
            ConfiguracaoCashback.objects.update_or_create(
                empresa=filial.empresa, filial=filial, defaults=dados,
            )
            messages.success(request, f"Configuração de {filial} salva.")

        return redirect(reverse("cashback:configuracao"))
