import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.core.paginator import Paginator
from django.db.models import Sum
from django.views.decorators.http import require_POST
from django.utils import timezone
from apps.core.models import Filial
from apps.core.services.permissions import requer_permissao
from apps.financeiro.forms import CentroCustoForm, FormaPagamentoForm, PlanoContasDespesaForm
from apps.financeiro.models import (
    CentroCusto, ContaReceber, ContaPagar, DocumentoFiscal, DREConsolidado,
    FormaPagamento, PlanoContas, TaxaParcelamento,
)


def _filial_ativa(request):
    return getattr(request, 'filial_ativa', None) or getattr(request, 'filial', None)


@requer_permissao('financeiro', 'ver')
def receber_list(request):
    qs = ContaReceber.objects.for_filial(_filial_ativa(request)).select_related(
        "cliente"
    ).order_by("data_vencimento")
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get("page", 1))

    totais = qs.aggregate(
        total=Sum("valor_final"),
        saldo=Sum("valor_saldo"),
    )
    return render(request, "financeiro/receber_list.html", {
        "title": "Contas a receber", "page": page, "totais": totais,
    })


@requer_permissao('financeiro', 'ver')
def pagar_list(request):
    qs = ContaPagar.objects.for_filial(_filial_ativa(request)).select_related(
        "fornecedor"
    ).order_by("data_vencimento")
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get("page", 1))
    return render(request, "financeiro/pagar_list.html", {
        "title": "Contas a pagar", "page": page,
    })


def _empresa_ativa(request):
    filial = _filial_ativa(request)
    return getattr(filial, "empresa", None) or getattr(request.user, "empresa", None)


def _pode_alterar_cadastros_financeiros(request):
    return (
        request.user.tem_permissao('financeiro', 'criar')
        or request.user.tem_permissao('financeiro', 'editar')
    )


@requer_permissao('financeiro', 'ver')
def centros_custo(request):
    empresa = _empresa_ativa(request)
    instance = None
    editar_id = request.GET.get("editar")
    if editar_id:
        instance = get_object_or_404(CentroCusto.objects.filter(empresa=empresa), pk=editar_id)

    if request.method == "POST":
        if not _pode_alterar_cadastros_financeiros(request):
            messages.error(request, "Usuário sem permissão para alterar cadastros financeiros.")
            return redirect("financeiro:centros_custo")
        acao = request.POST.get("acao")
        if acao == "excluir":
            obj = get_object_or_404(CentroCusto.objects.filter(empresa=empresa), pk=request.POST.get("id"))
            obj.ativo = False
            obj.save(update_fields=["ativo", "updated_at"])
            messages.success(request, "Centro de custo inativado.")
            return redirect("financeiro:centros_custo")
        if acao == "salvar":
            obj = None
            if request.POST.get("id"):
                obj = get_object_or_404(CentroCusto.objects.filter(empresa=empresa), pk=request.POST.get("id"))
            form = CentroCustoForm(request.POST, instance=obj, empresa=empresa)
            if form.is_valid():
                centro = form.save(commit=False)
                centro.empresa = empresa
                centro.save()
                messages.success(request, "Centro de custo salvo.")
                return redirect("financeiro:centros_custo")
            instance = obj
        else:
            form = CentroCustoForm(empresa=empresa)
    else:
        form = CentroCustoForm(instance=instance, empresa=empresa)

    centros = CentroCusto.objects.filter(empresa=empresa).order_by("codigo", "nome")
    return render(request, "financeiro/centros_custo.html", {
        "title": "Centros de custo",
        "form": form,
        "centros": centros,
        "instance": instance,
    })


@requer_permissao('financeiro', 'ver')
def plano_contas_despesas(request):
    empresa = _empresa_ativa(request)
    instance = None
    editar_id = request.GET.get("editar")
    if editar_id:
        instance = get_object_or_404(PlanoContas.objects.filter(empresa=empresa, tipo="D"), pk=editar_id)

    if request.method == "POST":
        if not _pode_alterar_cadastros_financeiros(request):
            messages.error(request, "Usuário sem permissão para alterar cadastros financeiros.")
            return redirect("financeiro:plano_contas_despesas")
        acao = request.POST.get("acao")
        if acao == "excluir":
            obj = get_object_or_404(PlanoContas.objects.filter(empresa=empresa, tipo="D"), pk=request.POST.get("id"))
            obj.ativo = False
            obj.save(update_fields=["ativo"])
            messages.success(request, "Despesa inativada.")
            return redirect("financeiro:plano_contas_despesas")
        if acao == "salvar":
            obj = None
            if request.POST.get("id"):
                obj = get_object_or_404(PlanoContas.objects.filter(empresa=empresa, tipo="D"), pk=request.POST.get("id"))
            form = PlanoContasDespesaForm(request.POST, instance=obj, empresa=empresa)
            if form.is_valid():
                form.save()
                messages.success(request, "Plano de contas de despesas salvo.")
                return redirect("financeiro:plano_contas_despesas")
            instance = obj
        else:
            form = PlanoContasDespesaForm(empresa=empresa)
    else:
        form = PlanoContasDespesaForm(instance=instance, empresa=empresa)

    contas = list(
        PlanoContas.objects.filter(empresa=empresa, tipo="D").select_related("conta_pai").order_by("codigo")
    )
    return render(request, "financeiro/plano_contas_despesas.html", {
        "title": "Plano de contas de despesas",
        "form": form,
        "contas": contas,
        "instance": instance,
    })


@requer_permissao('financeiro', 'ver')
def formas_pagamento(request):
    filial = _filial_ativa(request)
    empresa = _empresa_ativa(request)
    instance = None
    editar_id = request.GET.get("editar")
    if editar_id:
        instance = get_object_or_404(
            FormaPagamento.objects.filter(empresa=empresa, filial=filial),
            pk=editar_id,
        )

    if request.method == "POST":
        if not _pode_alterar_cadastros_financeiros(request):
            messages.error(request, "Usuário sem permissão para alterar cadastros financeiros.")
            return redirect("financeiro:formas_pagamento")
        acao = request.POST.get("acao")
        if acao == "excluir":
            obj = get_object_or_404(
                FormaPagamento.objects.filter(empresa=empresa, filial=filial),
                pk=request.POST.get("id"),
            )
            obj.ativo = False
            obj.save(update_fields=["ativo"])
            messages.success(request, "Forma de pagamento inativada.")
            return redirect("financeiro:formas_pagamento")
        if acao == "replicar":
            origem = get_object_or_404(
                FormaPagamento.objects.filter(empresa=empresa, filial=filial),
                pk=request.POST.get("id"),
            )
            destinos = Filial.objects.filter(
                empresa=empresa,
                pk__in=request.POST.getlist("filiais_destino"),
            ).exclude(pk=filial.pk)
            total = 0
            for destino in destinos:
                forma = FormaPagamento.objects.filter(
                    filial=destino,
                    descricao__iexact=origem.descricao,
                ).first()
                valores = {
                    "empresa": empresa,
                    "filial": destino,
                    "descricao": origem.descricao,
                    "tipo": origem.tipo,
                    "codigo_sefaz": origem.codigo_sefaz,
                    "requer_tef": origem.requer_tef,
                    "gera_parcelas": origem.gera_parcelas,
                    "prazo_liquidacao_dias": origem.prazo_liquidacao_dias,
                    "prazo_compensacao_dias_uteis": origem.prazo_compensacao_dias_uteis,
                    "taxa_administrativa": origem.taxa_administrativa,
                    "taxa_fixa": origem.taxa_fixa,
                    "tarifa_pagamento_fixa": origem.tarifa_pagamento_fixa,
                    "conta_bancaria_padrao": origem.conta_bancaria_padrao,
                    "movimenta_caixa": origem.movimenta_caixa,
                    "ativo": origem.ativo,
                }
                if forma:
                    for campo, valor in valores.items():
                        setattr(forma, campo, valor)
                    forma.save()
                else:
                    FormaPagamento.objects.create(**valores)
                total += 1
            messages.success(request, f"Forma de pagamento replicada para {total} filial(is).")
            return redirect("financeiro:formas_pagamento")
        if acao == "salvar":
            obj = None
            if request.POST.get("id"):
                obj = get_object_or_404(
                    FormaPagamento.objects.filter(empresa=empresa, filial=filial),
                    pk=request.POST.get("id"),
                )
            form = FormaPagamentoForm(request.POST, instance=obj, empresa=empresa, filial=filial)
            if form.is_valid():
                form.save()
                messages.success(request, "Forma de pagamento salva.")
                return redirect("financeiro:formas_pagamento")
            instance = obj
        else:
            form = FormaPagamentoForm(empresa=empresa, filial=filial)
    else:
        form = FormaPagamentoForm(instance=instance, empresa=empresa, filial=filial)

    formas = FormaPagamento.objects.filter(
        empresa=empresa,
        filial=filial,
    ).order_by("descricao")
    filiais_destino = Filial.objects.filter(empresa=empresa).exclude(pk=filial.pk).order_by(
        "nome_fantasia", "razao_social"
    )
    mostrar_form = bool(
        instance
        or request.GET.get("novo")
        or (request.method == "POST" and request.POST.get("acao") == "salvar")
    )
    return render(request, "financeiro/formas_pagamento.html", {
        "title": "Formas de pagamento",
        "form": form,
        "formas": formas,
        "instance": instance,
        "filiais_destino": filiais_destino,
        "mostrar_form": mostrar_form,
    })


@requer_permissao('financeiro', 'ver')
def api_taxas_forma_pagamento(request, pk):
    empresa = _empresa_ativa(request)
    filial = _filial_ativa(request)
    forma = get_object_or_404(FormaPagamento.objects.filter(empresa=empresa, filial=filial), pk=pk)

    if request.method == 'GET':
        taxas = list(forma.taxas_parcelamento.values('id', 'parcelas', 'bandeira', 'taxa'))
        return JsonResponse({'taxas': taxas})

    if not _pode_alterar_cadastros_financeiros(request):
        return JsonResponse({'erro': 'Sem permissão'}, status=403)

    if request.method == 'POST':
        data = json.loads(request.body)
        parcelas = int(data.get('parcelas', 0))
        bandeira = FormaPagamento.normalizar_bandeira(data.get('bandeira', ''))
        taxa = data.get('taxa', 0)
        if not (1 <= parcelas <= 24):
            return JsonResponse({'erro': 'Parcelas deve ser entre 1 e 24'}, status=400)
        try:
            taxa = Decimal(str(taxa))
        except (TypeError, ValueError, InvalidOperation):
            return JsonResponse({'erro': 'Informe uma taxa valida'}, status=400)
        if taxa < 0 or taxa > 100:
            return JsonResponse({'erro': 'A taxa deve ficar entre 0% e 100%'}, status=400)
        obj, _ = TaxaParcelamento.objects.update_or_create(
            forma_pagamento=forma, parcelas=parcelas, bandeira=bandeira,
            defaults={'taxa': taxa},
        )
        return JsonResponse({'id': obj.pk, 'parcelas': obj.parcelas, 'bandeira': obj.bandeira, 'taxa': str(obj.taxa)})

    if request.method == 'DELETE':
        data = json.loads(request.body)
        taxa_pk = data.get('id')
        TaxaParcelamento.objects.filter(forma_pagamento=forma, pk=taxa_pk).delete()
        return JsonResponse({'ok': True})

    return JsonResponse({'erro': 'Método não permitido'}, status=405)


@requer_permissao('fiscal', 'ver')
def documentos_fiscais_list(request):
    qs = DocumentoFiscal.objects.for_filial(_filial_ativa(request)).order_by("-data_emissao")
    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page", 1))
    return render(request, "financeiro/documentos_fiscais.html", {
        "title": "Documentos fiscais", "page": page,
    })


@requer_permissao('financeiro', 'ver')
def dre_view(request):
    """
    DRE gerencial do mês, calculado na leitura a partir dos títulos.

    NÃO lê o `DREConsolidado` gravado. Aquela tabela continua onde está
    porque o Analytics a consome, mas ela é um retrato: nada a repopula
    hoje, e mesmo repopulada ficaria velha assim que alguém corrigisse uma
    baixa. Aqui a conta é refeita a cada abertura, sobre os títulos que
    existem naquele momento.
    """
    from apps.financeiro.services.dre import (
        DREService, mes_anterior as dre_mes_anterior,
        primeiro_dia, proximo_mes as dre_proximo_mes,
    )

    hoje = timezone.localdate()
    # `?mes=AAAA-MM` vem da barra de endereços e chega com qualquer coisa;
    # o mês corrente é o padrão de quem só abriu a tela.
    texto = (request.GET.get("mes") or "").strip()
    try:
        ano, mes = texto.split("-")
        competencia = date(int(ano), int(mes), 1)
    except (ValueError, TypeError):
        competencia = primeiro_dia(hoje)

    dados = DREService.painel(
        _filial_ativa(request), competencia,
        regime=(request.GET.get("regime") or "").strip(),
    )
    return render(request, "financeiro/dre.html", {
        "title": "DRE Consolidado",
        "mes_texto": competencia.strftime("%Y-%m"),
        "mes_anterior_texto": (
            dre_mes_anterior(competencia).strftime("%Y-%m")
        ),
        "mes_seguinte_texto": dre_proximo_mes(competencia).strftime("%Y-%m"),
        # Navegar para o futuro num DRE não tem uso: o mês que ainda não
        # aconteceu não tem resultado a mostrar.
        "tem_seguinte": dre_proximo_mes(competencia) <= primeiro_dia(hoje),
        **dados,
    })
