"""Consulta da carteira/extrato de cashback do cliente e ajuste manual."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.cadastros.models import Cliente
from apps.core.services.permissions import PermissaoRequiredMixin

from ..models import CarteiraCashback, MovimentoCashback
from ..services.wallet_service import CashbackWalletService


class CarteiraCashbackBuscaView(PermissaoRequiredMixin, View):
    """Busca um cliente (por nome/CPF/CNPJ) para ver sua carteira de cashback."""

    permissao_modulo = "cashback"
    permissao_acao = "ver"

    def get(self, request):
        q = request.GET.get("q", "").strip()
        filial = request.filial_ativa
        resultados = []
        if len(q) >= 2:
            resultados = list(
                Cliente.objects.for_filial(filial)
                .filter(Q(razao_social__icontains=q) | Q(nome_fantasia__icontains=q) | Q(cpf_cnpj__icontains=q))
                .order_by("razao_social")[:20]
            )
        return render(request, "cashback/carteira_busca.html", {
            "title": "Carteira de Cashback",
            "q": q,
            "resultados": resultados,
        })


class CarteiraCashbackDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = "cashback"
    permissao_acao = "ver"

    def get(self, request, cliente_id):
        empresa = request.filial_ativa.empresa
        cliente = get_object_or_404(Cliente, pk=cliente_id)
        carteira = CarteiraCashback.objects.filter(empresa=empresa, cliente=cliente).first()

        if carteira:
            CashbackWalletService.expirar_creditos(carteira=carteira)
            carteira.refresh_from_db()

        movimentos_qs = MovimentoCashback.objects.filter(
            empresa=empresa, cliente=cliente,
        ).select_related("venda", "usuario", "filial").order_by("-created_at")
        page = Paginator(movimentos_qs, 30).get_page(request.GET.get("page"))

        return render(request, "cashback/carteira_detail.html", {
            "title": f"Carteira de Cashback — {cliente}",
            "cliente": cliente,
            "carteira": carteira,
            "page_obj": page,
        })

    def post(self, request, cliente_id):
        """Ajuste manual (positivo) de saldo."""
        empresa = request.filial_ativa.empresa
        cliente = get_object_or_404(Cliente, pk=cliente_id)

        try:
            valor = Decimal(request.POST.get("valor", "0").replace(",", "."))
        except InvalidOperation:
            messages.error(request, "Valor inválido.")
            return redirect(reverse("cashback:carteira-detail", args=[cliente_id]))

        observacao = request.POST.get("observacao", "").strip()
        if not observacao:
            messages.error(request, "Informe a observação do ajuste manual.")
            return redirect(reverse("cashback:carteira-detail", args=[cliente_id]))

        try:
            CashbackWalletService.ajustar_manual(
                empresa=empresa, cliente=cliente, valor=valor,
                usuario=request.user, observacao=observacao, request=request,
            )
            messages.success(request, f"Ajuste de R$ {valor} lançado na carteira de {cliente}.")
        except Exception as exc:
            messages.error(request, str(exc))

        return redirect(reverse("cashback:carteira-detail", args=[cliente_id]))
