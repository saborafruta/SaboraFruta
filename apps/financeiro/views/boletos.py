"""Operação de remessa e retorno de boletos."""
from django.contrib import messages
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.financeiro.constants.enums import StatusContaReceber, TipoFormaPagamento
from apps.financeiro.models import (
    ContaBancaria, ContaReceber, RemessaBancaria, RetornoBancario,
)
from apps.financeiro.services.cnab_cobranca import RemessaCNAB240Service, RetornoCNABService


class BoletoComunicacaoListView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "ver"
    template_name = "financeiro/boletos/comunicacao.html"

    def get(self, request):
        filial = request.filial_ativa
        contas = ContaBancaria.objects.filter(filial=filial, ativo=True).order_by("banco_nome", "descricao")
        conta_id = request.GET.get("conta_bancaria")
        conta = contas.filter(pk=conta_id).first() if conta_id else contas.first()
        titulos = ContaReceber.objects.none()
        if conta:
            titulos = ContaReceber.objects.filter(
                filial=filial,
                status__in=[StatusContaReceber.ABERTO, StatusContaReceber.VENCIDO, StatusContaReceber.NEGOCIADO],
                forma_pagamento__tipo=TipoFormaPagamento.BOLETO,
            ).filter(Q(conta_bancaria=conta) | Q(conta_bancaria__isnull=True)).select_related("cliente").order_by("data_vencimento")[:300]
        return render(request, self.template_name, {
            "title": "Boletos · Remessa e Retorno", "contas_bancarias": contas,
            "conta_bancaria": conta, "titulos": titulos,
            "remessas": RemessaBancaria.objects.filter(filial=filial).select_related("conta_bancaria", "usuario").order_by("-created_at")[:20],
            "retornos": RetornoBancario.objects.filter(filial=filial).select_related("conta_bancaria", "usuario").order_by("-created_at")[:20],
        })


class BoletoRemessaGerarView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "criar"

    def post(self, request):
        filial = request.filial_ativa
        conta = get_object_or_404(ContaBancaria, pk=request.POST.get("conta_bancaria"), filial=filial, ativo=True)
        ids = request.POST.getlist("titulos")
        try:
            remessa = RemessaCNAB240Service.gerar(conta_bancaria=conta, titulos=ids, usuario=request.user)
        except DomainError as exc:
            messages.error(request, str(exc))
        else:
            aviso = " Arquivo ainda não homologado pelo banco." if not conta.remessa_homologada else ""
            messages.success(request, f"Remessa {remessa.numero_remessa} gerada com {remessa.quantidade_boletos} boleto(s).{aviso}")
        return redirect(reverse("financeiro:boleto_comunicacao") + f"?conta_bancaria={conta.pk}")


class BoletoRetornoImportarView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "editar"

    def post(self, request):
        filial = request.filial_ativa
        conta = get_object_or_404(ContaBancaria, pk=request.POST.get("conta_bancaria"), filial=filial, ativo=True)
        arquivo = request.FILES.get("arquivo")
        if not arquivo:
            messages.error(request, "Selecione o arquivo de retorno.")
        else:
            try:
                retorno = RetornoCNABService.importar(
                    conta_bancaria=conta, nome=arquivo.name, dados=arquivo.read(), usuario=request.user,
                )
            except DomainError as exc:
                messages.error(request, str(exc))
            else:
                r = retorno.resumo_processamento
                messages.success(request, f"Retorno processado: {r['pagos']} pago(s), {r['rejeitados']} rejeitado(s) e {r['nao_encontrados']} não localizado(s).")
        return redirect(reverse("financeiro:boleto_comunicacao") + f"?conta_bancaria={conta.pk}")


class RemessaDownloadView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "ver"

    def get(self, request, pk):
        remessa = get_object_or_404(RemessaBancaria, pk=pk, filial=request.filial_ativa)
        if not remessa.arquivo_cnab:
            raise Http404
        resposta = HttpResponse(remessa.arquivo_cnab.encode("ascii"), content_type="text/plain; charset=us-ascii")
        resposta["Content-Disposition"] = f'attachment; filename="{remessa.arquivo_nome}"'
        return resposta


class RetornoDetalheView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "ver"

    def get(self, request, pk):
        retorno = get_object_or_404(
            RetornoBancario.objects.select_related("conta_bancaria"), pk=pk, filial=request.filial_ativa,
        )
        return render(request, "financeiro/boletos/retorno_detalhe.html", {
            "title": f"Retorno {retorno.arquivo_nome}", "retorno": retorno,
            "itens": retorno.itens.select_related("boleto", "boleto__conta_receber", "boleto__conta_receber__cliente"),
        })
