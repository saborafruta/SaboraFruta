from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.models import RegistroAuditoria
from apps.core.services.auditoria import auditoria_para_objeto, registrar_auditoria, snapshot_modelo
from apps.financeiro.forms import (
    ContaBancariaForm,
    DirecionarContaBancariaForm,
    EditarMovimentoBancarioForm,
    MovimentoContaBancariaForm,
)
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
    movimento_manual_id: int | None = None
    origem_codigo: str = ""
    registro_id: int | None = None

    @property
    def valor(self):
        return self.entrada - self.saida


@dataclass
class PendenciaContaBancaria:
    origem: str
    registro_id: int
    data: object
    descricao: str
    documento: str
    valor: Decimal


def _usuario_admin(request):
    perfil = getattr(request.user, "perfil", None)
    return request.user.is_superuser or bool(perfil and perfil.is_admin)


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
            antes = snapshot_modelo(instance) if instance else None
            form = ContaBancariaForm(request.POST, instance=instance, filial=filial)
            if form.is_valid():
                conta = form.save()
                registrar_auditoria(
                    request=request,
                    modulo=RegistroAuditoria.Modulo.FINANCEIRO,
                    acao=RegistroAuditoria.Acao.EDITAR if instance else RegistroAuditoria.Acao.CRIAR,
                    objeto=conta,
                    descricao=f"Conta bancaria {conta.descricao or conta.banco_nome} {'editada' if instance else 'criada'}",
                    antes=antes,
                    depois=snapshot_modelo(conta),
                    metadados={"contas_envolvidas": [conta.pk]},
                )
                messages.success(request, f"Conta {conta.descricao or conta.banco_nome} salva.")
                return redirect(reverse("financeiro:contas_bancarias"))
            return self._render(
                request,
                conta_form=form,
                conta_instance=instance,
                conta_modal_aberto=True,
            )

        if acao == "lancar_movimento":
            form = MovimentoContaBancariaForm(request.POST, filial=filial)
            if form.is_valid():
                self._salvar_movimento_manual(request, filial, form.cleaned_data)
                messages.success(request, "Movimento bancario registrado.")
                return redirect(reverse("financeiro:contas_bancarias"))
            return self._render(request, movimento_form=form, movimento_modal_aberto=True)

        if acao == "direcionar_pendencia":
            if not _usuario_admin(request):
                messages.error(request, "Apenas administradores podem direcionar movimentacoes antigas.")
                return redirect(reverse("financeiro:contas_bancarias"))
            form = DirecionarContaBancariaForm(request.POST, filial=filial)
            if form.is_valid():
                try:
                    conta = self._direcionar_pendencia(
                        request,
                        filial,
                        request.POST.get("origem_pendencia", ""),
                        request.POST.get("registro_id", ""),
                        form.cleaned_data["conta_bancaria"],
                    )
                except ValueError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, f"Movimentacao direcionada para {conta.descricao or conta.banco_nome}.")
                return redirect(reverse("financeiro:contas_bancarias"))
            messages.error(request, "Escolha uma conta bancaria ativa.")
            return redirect(reverse("financeiro:contas_bancarias"))

        if acao == "editar_movimento":
            if not _usuario_admin(request):
                messages.error(request, "Apenas administradores podem editar ajustes bancarios.")
                return redirect(reverse("financeiro:contas_bancarias"))
            movimento = get_object_or_404(
                ExtratoBancario.objects.filter(filial=filial, origem="manual"),
                pk=request.POST.get("movimento_id"),
            )
            form = EditarMovimentoBancarioForm(request.POST, filial=filial)
            if form.is_valid():
                self._editar_movimento_manual(request, movimento, form.cleaned_data)
                messages.success(request, "Ajuste bancario atualizado e registrado no log.")
                return redirect(reverse("financeiro:contas_bancarias") + f"?conta={form.cleaned_data['conta_bancaria'].pk}")
            return self._render(request, editar_movimento=movimento, editar_movimento_form=form, editar_movimento_modal_aberto=True)

        if acao == "alterar_conta_movimento":
            if not _usuario_admin(request):
                messages.error(request, "Apenas administradores podem alterar a conta de uma movimentacao.")
                return redirect(reverse("financeiro:contas_bancarias"))
            form = DirecionarContaBancariaForm(request.POST, filial=filial)
            if form.is_valid():
                conta = None
                try:
                    conta = self._alterar_conta_movimento(
                        request,
                        filial,
                        request.POST.get("origem_movimento", ""),
                        request.POST.get("movimento_id", ""),
                        form.cleaned_data["conta_bancaria"],
                        form.cleaned_data.get("justificativa") or "Conta bancaria corrigida.",
                    )
                except ValueError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, f"Movimentacao transferida para {conta.descricao or conta.banco_nome}.")
                destino = f"?conta={conta.pk}" if conta else ""
                return redirect(reverse("financeiro:contas_bancarias") + destino)
            messages.error(request, "Escolha uma conta bancaria ativa.")
            return redirect(reverse("financeiro:contas_bancarias"))

        messages.error(request, "Acao invalida.")
        return redirect(reverse("financeiro:contas_bancarias"))

    def _render(
        self,
        request,
        conta_form=None,
        conta_instance=None,
        movimento_form=None,
        movimento_modal_aberto=False,
        conta_modal_aberto=False,
        editar_movimento=None,
        editar_movimento_form=None,
        editar_movimento_modal_aberto=False,
        detalhe_movimento=None,
        detalhe_movimento_modal_aberto=False,
    ):
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
        if editar_movimento is None and request.GET.get("editar_movimento") and _usuario_admin(request):
            editar_movimento = get_object_or_404(
                ExtratoBancario.objects.filter(filial=filial, origem="manual"),
                pk=request.GET.get("editar_movimento"),
            )
        if editar_movimento_form is None and editar_movimento is not None:
            editar_movimento_form = EditarMovimentoBancarioForm(
                initial={
                    "conta_bancaria": editar_movimento.conta_bancaria,
                    "data_lancamento": editar_movimento.data_lancamento,
                    "valor": editar_movimento.valor,
                    "historico": editar_movimento.historico,
                    "documento": editar_movimento.documento,
                },
                filial=filial,
            )

        if detalhe_movimento is None and request.GET.get("movimento_origem") and request.GET.get("movimento_id"):
            try:
                detalhe_movimento = self._detalhar_movimento(
                    filial,
                    request.GET["movimento_origem"],
                    request.GET["movimento_id"],
                )
            except ValueError:
                messages.error(request, "Movimentacao nao encontrada.")

        pendencias = self._pendencias_sem_conta(filial)
        logs = RegistroAuditoria.objects.filter(
            filial=filial,
            modulo=RegistroAuditoria.Modulo.FINANCEIRO,
        ).select_related("usuario").order_by("-criado_em")
        logs = self._logs_bancarios(logs, conta_selecionada)

        return render(request, self.template_name, {
            "title": "Contas Bancarias",
            "contas": contas,
            "conta_form": conta_form,
            "conta_instance": conta_instance,
            "conta_modal_aberto": conta_modal_aberto or conta_instance is not None,
            "conta_selecionada": conta_selecionada,
            "movimento_form": movimento_form,
            "movimento_modal_aberto": movimento_modal_aberto,
            "editar_movimento": editar_movimento,
            "editar_movimento_form": editar_movimento_form,
            "editar_movimento_modal_aberto": editar_movimento_modal_aberto or editar_movimento is not None,
            "detalhe_movimento": detalhe_movimento,
            "detalhe_movimento_modal_aberto": detalhe_movimento_modal_aberto or detalhe_movimento is not None,
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
            "sem_conta": pendencias,
            "pendencias": pendencias["items"],
            "direcionar_conta_form": DirecionarContaBancariaForm(filial=filial),
            "user_is_admin": _usuario_admin(request),
            "logs_bancarios": logs[:50],
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
                    movimento_manual_id=item.pk if item.origem == "manual" else None,
                    origem_codigo="manual",
                    registro_id=item.pk,
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
                    origem_codigo="receber",
                    registro_id=item.pk,
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
                    origem_codigo="pagar",
                    registro_id=item.pk,
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
                    forma_pagamento__movimenta_caixa=True,
                ).exclude(venda_pdv__status="cancelada").select_related(
                    "venda_pdv", "forma_pagamento", "forma_pagamento__conta_bancaria_padrao", "conta_bancaria",
                )
                if busca:
                    qs = qs.filter(venda_pdv__numero_venda__icontains=busca)
                for item in qs:
                    conta_destino = item.conta_bancaria or item.forma_pagamento.conta_bancaria_padrao
                    if not conta_destino or conta_destino.pk not in conta_ids:
                        continue
                    valor = (item.valor or Decimal("0")) - (item.troco or Decimal("0"))
                    if valor <= 0:
                        continue
                    movimentos.append(MovimentoBancario(
                        data=timezone.localtime(item.venda_pdv.data_venda).date(),
                        conta=conta_destino,
                        historico=f"Venda PDV #{item.venda_pdv.numero_venda} - {item.forma_pagamento.descricao}",
                        origem="Venda PDV",
                        documento=str(item.venda_pdv.numero_venda),
                        entrada=valor,
                    saida=Decimal("0"),
                    origem_codigo="venda",
                    registro_id=item.pk,
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

    def _buscar_movimento_origem(self, filial, origem, registro_id):
        if not registro_id or not str(registro_id).isdigit():
            raise ValueError("Movimentacao invalida.")
        registro_id = int(registro_id)
        if origem == "manual":
            return get_object_or_404(ExtratoBancario.objects.filter(filial=filial), pk=registro_id)
        if origem == "receber":
            return get_object_or_404(ContaReceber.objects.for_filial(filial), pk=registro_id)
        if origem == "pagar":
            return get_object_or_404(PagamentoContaPagar.objects.for_filial(filial), pk=registro_id)
        if origem == "venda":
            from apps.pdv.models import PagamentoVendaPDV
            return get_object_or_404(PagamentoVendaPDV.objects.filter(venda_pdv__filial=filial), pk=registro_id)
        raise ValueError("Origem de movimentacao invalida.")

    def _detalhar_movimento(self, filial, origem, registro_id):
        item = self._buscar_movimento_origem(filial, origem, registro_id)
        conta = getattr(item, "conta_bancaria", None)
        if origem == "manual":
            valor = item.valor or Decimal("0")
            descricao = item.historico or "Lancamento manual"
            documento = item.documento
            data = item.data_lancamento
            referencia_url = ""
            origem_label = "Manual" if item.origem == "manual" else "Extrato"
        elif origem == "receber":
            valor = item.valor_pago or Decimal("0")
            descricao = f"Recebimento - {item.cliente}"
            documento = item.documento_numero
            data = item.data_pagamento
            referencia_url = reverse("financeiro:receber_detail", args=[item.pk])
            origem_label = "Conta a receber"
        elif origem == "pagar":
            valor = -(item.valor_liquido or Decimal("0"))
            descricao = f"Pagamento - {item.conta_pagar.beneficiario_nome}"
            documento = item.conta_pagar.documento_numero or item.referencia_pagamento
            data = item.data_pagamento
            referencia_url = reverse("financeiro:pagar_detail", args=[item.conta_pagar_id])
            origem_label = "Conta a pagar"
        else:
            conta = item.conta_bancaria or item.forma_pagamento.conta_bancaria_padrao
            valor = (item.valor or Decimal("0")) - (item.troco or Decimal("0"))
            descricao = f"Venda PDV #{item.venda_pdv.numero_venda} - {item.forma_pagamento.descricao}"
            documento = str(item.venda_pdv.numero_venda)
            data = timezone.localtime(item.venda_pdv.data_venda).date()
            referencia_url = ""
            origem_label = "Venda PDV"

        return {
            "item": item,
            "origem_codigo": origem,
            "origem": origem_label,
            "conta": conta,
            "descricao": descricao,
            "documento": documento,
            "data": data,
            "valor": valor,
            "referencia_url": referencia_url,
            "pode_editar_valor": origem == "manual" and item.origem == "manual",
            "logs": self._logs_bancarios(auditoria_para_objeto(item, limit=50)),
        }

    @staticmethod
    def _nome_campo_auditoria(campo):
        return {
            "conta_bancaria": "Conta bancaria",
            "data_lancamento": "Data",
            "valor": "Valor",
            "historico": "Historico",
            "documento": "Documento",
            "descricao": "Apelido",
            "saldo_inicial": "Saldo inicial",
            "ativo": "Situacao",
        }.get(campo, campo.replace("_", " ").capitalize())

    def _logs_bancarios(self, logs, conta=None):
        itens = []
        for log in logs:
            meta = log.metadados or {}
            contas = set(meta.get("contas_envolvidas") or [])
            if log.relacionado_tipo == "financeiro.contabancaria" and log.relacionado_id:
                contas.add(log.relacionado_id)
            if conta and conta.pk not in contas:
                continue
            anteriores = log.dados_anteriores or {}
            novos = log.dados_novos or {}
            alteracoes = []
            for campo in sorted(set(anteriores) | set(novos)):
                antes, depois = anteriores.get(campo), novos.get(campo)
                if antes != depois:
                    alteracoes.append({
                        "campo": self._nome_campo_auditoria(campo),
                        "antes": antes if antes not in (None, "") else "Nao informado",
                        "depois": depois if depois not in (None, "") else "Nao informado",
                    })
            itens.append({
                "data": log.criado_em,
                "usuario": log.usuario.nome if log.usuario else "Sistema",
                "acao": log.get_acao_display(),
                "descricao": log.objeto_descricao,
                "justificativa": log.justificativa,
                "alteracoes": alteracoes,
            })
        return itens

    def _pendencias_sem_conta(self, filial):
        itens = []
        receber_qs = ContaReceber.objects.filter(
            filial=filial, data_pagamento__isnull=False, valor_pago__gt=0, conta_bancaria__isnull=True,
        ).select_related("cliente")
        receber = Decimal("0")
        for item in receber_qs:
            valor = item.valor_pago or Decimal("0")
            receber += valor
            itens.append(PendenciaContaBancaria(
                origem="receber", registro_id=item.pk, data=item.data_pagamento,
                descricao=f"Recebimento - {item.cliente}", documento=item.documento_numero, valor=valor,
            ))

        pagar_qs = PagamentoContaPagar.objects.filter(
            filial=filial, conta_bancaria__isnull=True,
        ).select_related("conta_pagar__fornecedor", "conta_pagar__funcionario")
        pagar = Decimal("0")
        for item in pagar_qs:
            valor = item.valor_liquido
            pagar += valor
            itens.append(PendenciaContaBancaria(
                origem="pagar", registro_id=item.pk, data=item.data_pagamento,
                descricao=f"Pagamento - {item.conta_pagar.beneficiario_nome}",
                documento=item.conta_pagar.documento_numero or item.referencia_pagamento, valor=valor,
            ))

        vendas = Decimal("0")
        try:
            from apps.pdv.models import PagamentoVendaPDV
            vendas_qs = PagamentoVendaPDV.objects.filter(
                venda_pdv__filial=filial,
                forma_pagamento__movimenta_caixa=True,
                conta_bancaria__isnull=True,
                forma_pagamento__conta_bancaria_padrao__isnull=True,
            ).exclude(venda_pdv__status="cancelada").select_related("venda_pdv", "forma_pagamento")
            for item in vendas_qs:
                valor = max((item.valor or Decimal("0")) - (item.troco or Decimal("0")), Decimal("0"))
                if not valor:
                    continue
                vendas += valor
                itens.append(PendenciaContaBancaria(
                    origem="venda", registro_id=item.pk,
                    data=timezone.localtime(item.venda_pdv.data_venda).date(),
                    descricao=f"Venda PDV #{item.venda_pdv.numero_venda} - {item.forma_pagamento.descricao}",
                    documento=str(item.venda_pdv.numero_venda), valor=valor,
                ))
        except Exception:
            pass
        total = receber + pagar + vendas
        return {
            "receber": receber, "pagar": pagar, "vendas": vendas, "total": total,
            "items": sorted(itens, key=lambda item: (item.data, item.origem), reverse=True),
        }

    @transaction.atomic
    def _direcionar_pendencia(self, request, filial, origem, registro_id, conta):
        if not registro_id or not str(registro_id).isdigit():
            raise ValueError("Movimentacao invalida.")
        registro_id = int(registro_id)
        antes = None
        if origem == "receber":
            item = get_object_or_404(ContaReceber.objects.for_filial(filial), pk=registro_id, conta_bancaria__isnull=True)
            antes = snapshot_modelo(item, ["conta_bancaria"])
            item.conta_bancaria = conta
            item.save(update_fields=["conta_bancaria", "updated_at"])
        elif origem == "pagar":
            item = get_object_or_404(PagamentoContaPagar.objects.for_filial(filial), pk=registro_id, conta_bancaria__isnull=True)
            antes = snapshot_modelo(item, ["conta_bancaria"])
            item.conta_bancaria = conta
            item.save(update_fields=["conta_bancaria", "updated_at"])
            if item.conta_pagar.pagamentos.count() == 1:
                item.conta_pagar.conta_bancaria = conta
                item.conta_pagar.save(update_fields=["conta_bancaria", "updated_at"])
        elif origem == "venda":
            from apps.pdv.models import PagamentoVendaPDV
            item = get_object_or_404(
                PagamentoVendaPDV.objects.filter(venda_pdv__filial=filial, conta_bancaria__isnull=True),
                pk=registro_id,
            )
            antes = snapshot_modelo(item, ["conta_bancaria"])
            item.conta_bancaria = conta
            item.save(update_fields=["conta_bancaria"])
        else:
            raise ValueError("Origem de movimentacao invalida.")

        registrar_auditoria(
            request=request,
            modulo=RegistroAuditoria.Modulo.FINANCEIRO,
            acao=RegistroAuditoria.Acao.VINCULAR,
            objeto=item,
            relacionado=conta,
            descricao=f"{origem.title()} direcionado para conta bancaria",
            justificativa="Conta bancaria definida posteriormente.",
            antes=antes,
            depois=snapshot_modelo(item, ["conta_bancaria"]),
            metadados={"contas_envolvidas": [conta.pk], "origem_movimento": origem},
        )
        self._atualizar_saldo_conta(conta)
        return conta

    @transaction.atomic
    def _alterar_conta_movimento(self, request, filial, origem, registro_id, nova_conta, justificativa):
        item = self._buscar_movimento_origem(filial, origem, registro_id)
        conta_anterior = getattr(item, "conta_bancaria", None)
        if origem == "venda":
            conta_anterior = item.conta_bancaria or item.forma_pagamento.conta_bancaria_padrao
        if conta_anterior and conta_anterior.pk == nova_conta.pk:
            raise ValueError("A movimentacao ja esta nesta conta bancaria.")

        antes = snapshot_modelo(item, ["conta_bancaria"])
        item.conta_bancaria = nova_conta
        update_fields = ["conta_bancaria"]
        if hasattr(item, "updated_at"):
            update_fields.append("updated_at")
        item.save(update_fields=update_fields)
        if origem == "pagar" and item.conta_pagar.pagamentos.count() == 1:
            item.conta_pagar.conta_bancaria = nova_conta
            item.conta_pagar.save(update_fields=["conta_bancaria", "updated_at"])

        contas_envolvidas = [nova_conta.pk]
        if conta_anterior:
            contas_envolvidas.append(conta_anterior.pk)
        registrar_auditoria(
            request=request,
            modulo=RegistroAuditoria.Modulo.FINANCEIRO,
            acao=RegistroAuditoria.Acao.AJUSTAR,
            objeto=item,
            relacionado=nova_conta,
            descricao=f"{origem.title()} transferido entre contas bancarias",
            justificativa=justificativa,
            antes=antes,
            depois=snapshot_modelo(item, ["conta_bancaria"]),
            metadados={"contas_envolvidas": contas_envolvidas, "origem_movimento": origem},
        )
        if conta_anterior:
            self._atualizar_saldo_conta(conta_anterior)
        self._atualizar_saldo_conta(nova_conta)
        return nova_conta

    @transaction.atomic
    def _editar_movimento_manual(self, request, movimento, dados):
        conta_anterior = movimento.conta_bancaria
        antes = snapshot_modelo(movimento, ["conta_bancaria", "data_lancamento", "valor", "historico", "documento"])
        movimento.conta_bancaria = dados["conta_bancaria"]
        movimento.data_lancamento = dados["data_lancamento"]
        movimento.valor = dados["valor"]
        movimento.historico = dados["historico"]
        movimento.documento = dados["documento"]
        movimento.save(update_fields=["conta_bancaria", "data_lancamento", "valor", "historico", "documento"])
        registrar_auditoria(
            request=request,
            modulo=RegistroAuditoria.Modulo.FINANCEIRO,
            acao=RegistroAuditoria.Acao.AJUSTAR,
            objeto=movimento,
            relacionado=movimento.conta_bancaria,
            descricao="Ajuste manual bancario editado",
            justificativa=dados["justificativa"],
            antes=antes,
            depois=snapshot_modelo(movimento, ["conta_bancaria", "data_lancamento", "valor", "historico", "documento"]),
            metadados={"contas_envolvidas": [conta_anterior.pk, movimento.conta_bancaria_id]},
        )
        self._atualizar_saldo_conta(conta_anterior)
        if movimento.conta_bancaria_id != conta_anterior.pk:
            self._atualizar_saldo_conta(movimento.conta_bancaria)

    def _atualizar_saldo_conta(self, conta):
        conta.saldo_atual = self._saldo_calculado(conta)
        conta.save(update_fields=["saldo_atual", "updated_at"])

    def _salvar_movimento_manual(self, request, filial, dados):
        tipo = dados["tipo"]
        valor = dados["valor"]
        data = dados["data_lancamento"]
        historico = dados.get("historico") or dict(MovimentoContaBancariaForm.TIPO_CHOICES).get(tipo, "Movimento manual")
        documento = dados.get("documento") or ""

        def criar(conta, valor_movimento, texto):
            movimento = ExtratoBancario.objects.create(
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
            registrar_auditoria(
                request=request,
                modulo=RegistroAuditoria.Modulo.FINANCEIRO,
                acao=RegistroAuditoria.Acao.CRIAR,
                objeto=movimento,
                relacionado=conta,
                descricao="Ajuste manual bancario criado",
                depois=snapshot_modelo(movimento, ["conta_bancaria", "data_lancamento", "valor", "historico", "documento"]),
                metadados={"contas_envolvidas": [conta.pk]},
            )
            return movimento

        if tipo == MovimentoContaBancariaForm.TIPO_CREDITO:
            criar(dados["conta_destino"], valor, historico)
        elif tipo == MovimentoContaBancariaForm.TIPO_DEBITO:
            criar(dados["conta_origem"], -valor, historico)
        else:
            origem = dados["conta_origem"]
            destino = dados["conta_destino"]
            criar(origem, -valor, f"Transferencia para {destino.descricao or destino.banco_nome}. {historico}".strip())
            criar(destino, valor, f"Transferencia de {origem.descricao or origem.banco_nome}. {historico}".strip())
