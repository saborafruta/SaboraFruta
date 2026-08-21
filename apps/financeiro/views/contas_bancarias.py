from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.financeiro.forms import ContaBancariaForm, MovimentoContaBancariaForm
from apps.financeiro.models import ContaBancaria
from apps.financeiro.models.extrato import ExtratoBancario
from apps.financeiro.models.receber_pagar import ContaReceber, PagamentoContaPagar


@dataclass
class MovimentoBancario:
    data: object
    conta: ContaBancaria
    historico: str
    origem: str
    documento: str
    entrada: Decimal
    saida: Decimal
    referencia_url: str = ""

    @property
    def valor(self):
        return self.entrada - self.saida


class ContaBancariaListView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "ver"
    template_name = "financeiro/contas_bancarias/list.html"

    def get(self, request):
        return self._render(request)

    def post(self, request):
        acao = request.POST.get("acao")
        filial = request.filial_ativa

        if acao == "salvar_conta":
            instance = None
            if request.POST.get("id"):
                instance = get_object_or_404(ContaBancaria.objects.for_filial(filial), pk=request.POST.get("id"))
            form = ContaBancariaForm(request.POST, instance=instance, filial=filial)
            if form.is_valid():
                conta = form.save()
                messages.success(request, f"Conta {conta.descricao or conta.banco_nome} salva.")
                return redirect(reverse("financeiro:contas_bancarias"))
            return self._render(request, conta_form=form, conta_instance=instance)

        if acao == "lancar_movimento":
            form = MovimentoContaBancariaForm(request.POST, filial=filial)
            if form.is_valid():
                self._salvar_movimento_manual(filial, form.cleaned_data)
                messages.success(request, "Movimento bancario registrado.")
                return redirect(reverse("financeiro:contas_bancarias"))
            return self._render(request, movimento_form=form, movimento_modal_aberto=True)

        messages.error(request, "Acao invalida.")
        return redirect(reverse("financeiro:contas_bancarias"))

    def _render(self, request, conta_form=None, conta_instance=None, movimento_form=None, movimento_modal_aberto=False):
        filial = request.filial_ativa
        hoje = timezone.localdate()
        data_ini = parse_date(request.GET.get("data_ini", "")) or hoje.replace(day=1)
        data_fim = parse_date(request.GET.get("data_fim", "")) or hoje
        conta_id = request.GET.get("conta", "")
        origem = request.GET.get("origem", "")
        busca = (request.GET.get("q") or "").strip()

        contas = list(ContaBancaria.objects.for_filial(filial).order_by("-ativo", "descricao", "banco_nome"))
        conta_selecionada = None
        if conta_id:
            conta_selecionada = get_object_or_404(ContaBancaria.objects.for_filial(filial), pk=conta_id)

        movimentos = self._movimentos_periodo(
            request,
            filial=filial,
            contas=contas,
            data_ini=data_ini,
            data_fim=data_fim,
            conta=conta_selecionada,
            origem=origem,
            busca=busca,
        )

        entradas_periodo = sum((m.entrada for m in movimentos), Decimal("0"))
        saidas_periodo = sum((m.saida for m in movimentos), Decimal("0"))
        saldos = {conta.pk: self._saldo_calculado(conta) for conta in contas}
        for conta_item in contas:
            conta_item.saldo_calculado = saldos.get(conta_item.pk, Decimal("0"))
        saldo_total = sum(saldos.values(), Decimal("0"))

        page_obj = Paginator(movimentos, 60).get_page(request.GET.get("page"))

        if conta_form is None:
            editar_id = request.GET.get("editar")
            conta_instance = get_object_or_404(ContaBancaria.objects.for_filial(filial), pk=editar_id) if editar_id else None
            conta_form = ContaBancariaForm(instance=conta_instance, filial=filial)
        if movimento_form is None:
            movimento_form = MovimentoContaBancariaForm(filial=filial)

        return render(request, self.template_name, {
            "title": "Contas Bancarias",
            "contas": contas,
            "conta_form": conta_form,
            "conta_instance": conta_instance,
            "movimento_form": movimento_form,
            "movimento_modal_aberto": movimento_modal_aberto,
            "page_obj": page_obj,
            "data_ini": data_ini,
            "data_fim": data_fim,
            "conta_id": conta_id,
            "origem": origem,
            "busca": busca,
            "entradas_periodo": entradas_periodo,
            "saidas_periodo": saidas_periodo,
            "saldo_periodo": entradas_periodo - saidas_periodo,
            "saldo_total": saldo_total,
            "sem_conta": self._pendencias_sem_conta(filial),
        })

    def _movimentos_periodo(self, request, *, filial, contas, data_ini, data_fim, conta=None, origem="", busca=""):
        conta_ids = [conta.pk] if conta else [c.pk for c in contas]
        movimentos = []

        if not origem or origem == "manual":
            qs = ExtratoBancario.objects.filter(
                filial=filial,
                conta_bancaria_id__in=conta_ids,
                data_lancamento__range=(data_ini, data_fim),
            ).select_related("conta_bancaria")
            if busca:
                qs = qs.filter(historico__icontains=busca)
            for item in qs:
                valor = item.valor or Decimal("0")
                movimentos.append(MovimentoBancario(
                    data=item.data_lancamento,
                    conta=item.conta_bancaria,
                    historico=item.historico or "Lancamento manual",
                    origem="Manual" if item.origem == "manual" else "Extrato",
                    documento=item.documento,
                    entrada=max(valor, Decimal("0")),
                    saida=abs(min(valor, Decimal("0"))),
                ))

        if not origem or origem == "receber":
            qs = ContaReceber.objects.filter(
                filial=filial,
                conta_bancaria_id__in=conta_ids,
                data_pagamento__range=(data_ini, data_fim),
                valor_pago__gt=0,
            ).select_related("conta_bancaria", "cliente")
            if busca:
                qs = qs.filter(cliente__nome__icontains=busca)
            for item in qs:
                movimentos.append(MovimentoBancario(
                    data=item.data_pagamento,
                    conta=item.conta_bancaria,
                    historico=f"Recebimento - {item.cliente}",
                    origem="Conta a receber",
                    documento=item.documento_numero,
                    entrada=item.valor_pago or Decimal("0"),
                    saida=Decimal("0"),
                    referencia_url=reverse("financeiro:receber_detail", args=[item.pk]),
                ))

        if not origem or origem == "pagar":
            qs = PagamentoContaPagar.objects.filter(
                filial=filial,
                conta_bancaria_id__in=conta_ids,
                data_pagamento__range=(data_ini, data_fim),
            ).select_related("conta_bancaria", "conta_pagar__fornecedor", "conta_pagar__funcionario")
            if busca:
                qs = qs.filter(conta_pagar__documento_numero__icontains=busca)
            for item in qs:
                movimentos.append(MovimentoBancario(
                    data=item.data_pagamento,
                    conta=item.conta_bancaria,
                    historico=f"Pagamento - {item.conta_pagar.beneficiario_nome}",
                    origem="Conta a pagar",
                    documento=item.conta_pagar.documento_numero or item.referencia_pagamento,
                    entrada=Decimal("0"),
                    saida=item.valor_liquido,
                    referencia_url=reverse("financeiro:pagar_detail", args=[item.conta_pagar_id]),
                ))

        if not origem or origem == "venda":
            try:
                from apps.pdv.models import PagamentoVendaPDV
            except Exception:
                PagamentoVendaPDV = None
            if PagamentoVendaPDV:
                qs = PagamentoVendaPDV.objects.filter(
                    venda_pdv__filial=filial,
                    venda_pdv__data_venda__date__range=(data_ini, data_fim),
                    forma_pagamento__conta_bancaria_padrao_id__in=conta_ids,
                    forma_pagamento__movimenta_caixa=True,
                ).exclude(venda_pdv__status="cancelada").select_related(
                    "venda_pdv", "forma_pagamento", "forma_pagamento__conta_bancaria_padrao",
                )
                if busca:
                    qs = qs.filter(venda_pdv__numero_venda__icontains=busca)
                for item in qs:
                    valor = (item.valor or Decimal("0")) - (item.troco or Decimal("0"))
                    if valor <= 0:
                        continue
                    movimentos.append(MovimentoBancario(
                        data=timezone.localtime(item.venda_pdv.data_venda).date(),
                        conta=item.forma_pagamento.conta_bancaria_padrao,
                        historico=f"Venda PDV #{item.venda_pdv.numero_venda} - {item.forma_pagamento.descricao}",
                        origem="Venda PDV",
                        documento=str(item.venda_pdv.numero_venda),
                        entrada=valor,
                        saida=Decimal("0"),
                    ))

        return sorted(movimentos, key=lambda m: (m.data, m.origem, m.documento), reverse=True)

    def _saldo_calculado(self, conta):
        data_min = timezone.datetime.min.date()
        data_max = timezone.datetime.max.date()
        movimentos = self._movimentos_periodo(
            request=None,
            filial=conta.filial,
            contas=[conta],
            data_ini=data_min,
            data_fim=data_max,
            conta=conta,
        )
        if not movimentos and (conta.saldo_inicial or Decimal("0")) == Decimal("0") and (conta.saldo_atual or Decimal("0")) != Decimal("0"):
            return conta.saldo_atual
        return (conta.saldo_inicial or Decimal("0")) + sum((m.valor for m in movimentos), Decimal("0"))

    def _pendencias_sem_conta(self, filial):
        receber = ContaReceber.objects.filter(
            filial=filial, data_pagamento__isnull=False, valor_pago__gt=0, conta_bancaria__isnull=True,
        ).aggregate(total=Sum("valor_pago"))["total"] or Decimal("0")
        pagar = PagamentoContaPagar.objects.filter(
            filial=filial, conta_bancaria__isnull=True,
        ).aggregate(total=Sum("valor_pago"))["total"] or Decimal("0")
        vendas = Decimal("0")
        try:
            from apps.pdv.models import PagamentoVendaPDV
            vendas = PagamentoVendaPDV.objects.filter(
                venda_pdv__filial=filial,
                forma_pagamento__movimenta_caixa=True,
                forma_pagamento__conta_bancaria_padrao__isnull=True,
            ).exclude(venda_pdv__status="cancelada").aggregate(total=Sum("valor"))["total"] or Decimal("0")
        except Exception:
            pass
        total = receber + pagar + vendas
        return {"receber": receber, "pagar": pagar, "vendas": vendas, "total": total}

    def _salvar_movimento_manual(self, filial, dados):
        tipo = dados["tipo"]
        valor = dados["valor"]
        data = dados["data_lancamento"]
        historico = dados.get("historico") or dict(MovimentoContaBancariaForm.TIPO_CHOICES).get(tipo, "Movimento manual")
        documento = dados.get("documento") or ""

        def criar(conta, valor_movimento, texto):
            ExtratoBancario.objects.create(
                conta_bancaria=conta,
                filial=filial,
                data_lancamento=data,
                historico=texto,
                documento=documento,
                valor=valor_movimento,
                origem="manual",
                status="importado",
            )
            conta.saldo_atual = self._saldo_calculado(conta)
            conta.save(update_fields=["saldo_atual", "updated_at"])

        if tipo == MovimentoContaBancariaForm.TIPO_CREDITO:
            criar(dados["conta_destino"], valor, historico)
        elif tipo == MovimentoContaBancariaForm.TIPO_DEBITO:
            criar(dados["conta_origem"], -valor, historico)
        else:
            origem = dados["conta_origem"]
            destino = dados["conta_destino"]
            criar(origem, -valor, f"Transferencia para {destino.descricao or destino.banco_nome}. {historico}".strip())
            criar(destino, valor, f"Transferencia de {origem.descricao or origem.banco_nome}. {historico}".strip())
