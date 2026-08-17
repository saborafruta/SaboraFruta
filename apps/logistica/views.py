import json
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.cadastros.models import Cliente, Fornecedor, Motorista, Veiculo
from apps.core.models.empresa import Filial
from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.fiscal.integrations.focusnfe.exceptions import FocusNFeError
from apps.financeiro.models.fiscal import DocumentoFiscal
from apps.estoque.models import MovimentacaoEstoque
from apps.logistica.forms import (
    CTeForm,
    DocumentoCTeForm,
    DocumentoMDFeForm,
    DocumentoManifestoCargaForm,
    ItemOrdemColetaForm,
    ItemPedidoExpedicaoForm,
    ItemRomaneioCargaForm,
    ManifestoCargaForm,
    MDFeForm,
    OrdemColetaForm,
    PedidoExpedicaoForm,
    RomaneioCargaForm,
)
from apps.logistica.models import (
    CTe,
    DocumentoCTe,
    DocumentoMDFe,
    DocumentoManifestoCarga,
    ItemOrdemColeta,
    ItemPedidoExpedicao,
    ItemRomaneioCarga,
    ManifestoCarga,
    MDFe,
    OrdemColeta,
    PedidoExpedicao,
    RomaneioCarga,
)


logger = logging.getLogger(__name__)


def _filial(request):
    return request.filial_ativa


def _clientes_fornecedores_json(filial):
    """Retorna JSON com clientes e fornecedores ativos da filial para autocomplete."""
    clientes = list(
        Cliente.objects.for_filial(filial).filter(ativo=True)
        .values('id', 'razao_social', 'nome_fantasia', 'cpf_cnpj')
        .order_by('razao_social')
    )
    fornecedores = list(
        Fornecedor.objects.for_filial(filial).filter(ativo=True)
        .values('id', 'razao_social', 'nome_fantasia', 'cpf_cnpj')
        .order_by('razao_social')
    )
    return json.dumps(clientes, ensure_ascii=False), json.dumps(fornecedores, ensure_ascii=False)


def _motoristas_veiculos_json(filial):
    """Retorna JSON com motoristas e veículos ativos da filial para os forms."""
    motoristas = list(
        Motorista.objects.for_filial(filial).filter(ativo=True)
        .values('id', 'nome', 'cpf', 'cnh')
        .order_by('nome')
    )
    veiculos = list(
        Veiculo.objects.for_filial(filial).filter(ativo=True)
        .values(
            'id', 'placa', 'descricao', 'marca', 'modelo', 'renavam',
            'uf_placa', 'tipo_rodado', 'tipo_carroceria', 'tara',
            'capacidade_kg', 'transportadora__rntrc',
        )
        .order_by('placa')
    )
    return json.dumps(motoristas, ensure_ascii=False), json.dumps(
        veiculos, ensure_ascii=False, default=str
    )


def _proximo_numero(filial):
    ultimo = (
        RomaneioCarga.objects.for_filial(filial)
        .order_by("-numero")
        .values_list("numero", flat=True)
        .first()
    )
    return (ultimo or 0) + 1


def _proximo_numero_ordem_coleta(filial):
    ultimo = (
        OrdemColeta.objects.for_filial(filial)
        .order_by("-numero")
        .values_list("numero", flat=True)
        .first()
    )
    return (ultimo or 0) + 1


def _proximo_numero_manifesto(filial):
    ultimo = (
        ManifestoCarga.objects.for_filial(filial)
        .order_by("-numero")
        .values_list("numero", flat=True)
        .first()
    )
    return (ultimo or 0) + 1


def _proximo_numero_cte(filial):
    ultimo = (
        CTe.objects.for_filial(filial)
        .order_by("-numero")
        .values_list("numero", flat=True)
        .first()
    )
    return (ultimo or 0) + 1


class RomaneioCargaListView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    template_name = "logistica/romaneio/list.html"

    def get(self, request):
        filial = _filial(request)
        qs = (
            RomaneioCarga.objects.for_filial(filial)
            .select_related("transportadora", "responsavel")
            .annotate(qtd_itens=Count("itens"))
        )

        status = request.GET.get("status", "")
        q = request.GET.get("q", "").strip()
        data_ini = request.GET.get("data_ini", "")
        data_fim = request.GET.get("data_fim", "")

        if status:
            qs = qs.filter(status=status)
        if q:
            qs = qs.filter(
                Q(numero__icontains=q)
                | Q(motorista_nome__icontains=q)
                | Q(veiculo_placa__icontains=q)
                | Q(destino_rota__icontains=q)
                | Q(transportadora__razao_social__icontains=q)
                | Q(transportadora__nome_fantasia__icontains=q)
            )
        if data_ini:
            qs = qs.filter(data__gte=data_ini)
        if data_fim:
            qs = qs.filter(data__lte=data_fim)

        kpis = qs.aggregate(
            total=Count("id"),
            itens=Count("itens"),
            peso=Sum("peso_total_kg"),
            valor=Sum("valor_total"),
        )

        page_obj = Paginator(qs, 30).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "title": "Romaneio de Carga",
            "romaneios": page_obj.object_list,
            "page_obj": page_obj,
            "status_choices": RomaneioCarga.Status.choices,
            "status_filtro": status,
            "q": q,
            "data_ini": data_ini,
            "data_fim": data_fim,
            "kpis": kpis,
        })


class RomaneioCargaCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "criar"
    template_name = "logistica/romaneio/form.html"

    def get(self, request):
        filial = _filial(request)
        form = RomaneioCargaForm(filial=filial, initial={
            "numero": _proximo_numero(filial),
            "data": timezone.localdate(),
        })
        motoristas_json, veiculos_json = _motoristas_veiculos_json(filial)
        return render(request, self.template_name, {
            "title": "Novo Romaneio de Carga",
            "form": form,
            "cancel_url": reverse("logistica:romaneio-list"),
            "motoristas_json": motoristas_json,
            "veiculos_json": veiculos_json,
        })

    def post(self, request):
        filial = _filial(request)
        form = RomaneioCargaForm(request.POST, filial=filial)
        if form.is_valid():
            romaneio = form.save(commit=False)
            romaneio.filial = filial
            romaneio.responsavel = request.user
            romaneio.save()
            messages.success(request, f"Romaneio #{romaneio.numero:06d} criado.")
            return redirect("logistica:romaneio-detail", pk=romaneio.pk)
        motoristas_json, veiculos_json = _motoristas_veiculos_json(filial)
        return render(request, self.template_name, {
            "title": "Novo Romaneio de Carga",
            "form": form,
            "cancel_url": reverse("logistica:romaneio-list"),
            "motoristas_json": motoristas_json,
            "veiculos_json": veiculos_json,
        })


class RomaneioCargaUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"
    template_name = "logistica/romaneio/form.html"

    def get(self, request, pk):
        filial = _filial(request)
        romaneio = get_object_or_404(RomaneioCarga.objects.for_filial(filial), pk=pk)
        form = RomaneioCargaForm(instance=romaneio, filial=filial)
        motoristas_json, veiculos_json = _motoristas_veiculos_json(filial)
        return render(request, self.template_name, {
            "title": f"Editar Romaneio #{romaneio.numero:06d}",
            "form": form,
            "romaneio": romaneio,
            "cancel_url": reverse("logistica:romaneio-detail", kwargs={"pk": romaneio.pk}),
            "motoristas_json": motoristas_json,
            "veiculos_json": veiculos_json,
        })

    def post(self, request, pk):
        romaneio = get_object_or_404(RomaneioCarga.objects.for_filial(_filial(request)), pk=pk)
        form = RomaneioCargaForm(request.POST, instance=romaneio, filial=_filial(request))
        if form.is_valid():
            form.save()
            messages.success(request, f"Romaneio #{romaneio.numero:06d} atualizado.")
            return redirect("logistica:romaneio-detail", pk=romaneio.pk)
        return render(request, self.template_name, {
            "title": f"Editar Romaneio #{romaneio.numero:06d}",
            "form": form,
            "romaneio": romaneio,
            "cancel_url": reverse("logistica:romaneio-detail", kwargs={"pk": romaneio.pk}),
        })


class RomaneioCargaDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    template_name = "logistica/romaneio/detail.html"

    def get(self, request, pk):
        romaneio = get_object_or_404(
            RomaneioCarga.objects.for_filial(_filial(request)).select_related("transportadora", "responsavel"),
            pk=pk,
        )
        itens = romaneio.itens.all()
        item_form = ItemRomaneioCargaForm(initial={"ordem": itens.count() + 1})
        return render(request, self.template_name, {
            "title": f"Romaneio #{romaneio.numero:06d}",
            "romaneio": romaneio,
            "itens": itens,
            "item_form": item_form,
        })


class RomaneioAlterarStatusView(PermissaoRequiredMixin, View):
    """Altera o status de um Romaneio de Carga via POST rápido (sem form completo)."""
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    TRANSICOES_VALIDAS = {
        "rascunho": ["em_carregamento", "cancelado"],
        "em_carregamento": ["em_rota", "rascunho", "cancelado"],
        "em_rota": ["entregue", "em_carregamento", "cancelado"],
        "entregue": ["em_rota"],
        "cancelado": ["rascunho"],
    }

    def post(self, request, pk):
        romaneio = get_object_or_404(RomaneioCarga.objects.for_filial(_filial(request)), pk=pk)
        novo_status = request.POST.get("status", "").strip()
        validos = self.TRANSICOES_VALIDAS.get(romaneio.status, [])
        if novo_status in validos:
            romaneio.status = novo_status
            romaneio.save(update_fields=["status", "updated_at"])
            messages.success(request, f"Status do Romaneio #{romaneio.numero:06d} alterado para «{romaneio.get_status_display()}».")
        else:
            messages.error(request, "Transição de status inválida.")
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "logistica:romaneio-list"
        return redirect(next_url)


class ItemRomaneioCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        romaneio = get_object_or_404(RomaneioCarga.objects.for_filial(_filial(request)), pk=pk)
        form = ItemRomaneioCargaForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.romaneio = romaneio
            item.save()
            romaneio.recalcular_totais()
            messages.success(request, "Entrega adicionada ao romaneio.")
        else:
            messages.error(request, "Revise os dados da entrega do romaneio.")
        return redirect("logistica:romaneio-detail", pk=romaneio.pk)


class ItemRomaneioDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk, item_pk):
        romaneio = get_object_or_404(RomaneioCarga.objects.for_filial(_filial(request)), pk=pk)
        item = get_object_or_404(ItemRomaneioCarga.objects.filter(romaneio=romaneio), pk=item_pk)
        item.delete()
        romaneio.recalcular_totais()
        messages.success(request, "Entrega removida do romaneio.")
        return redirect("logistica:romaneio-detail", pk=romaneio.pk)


class OrdemColetaListView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    template_name = "logistica/ordem_coleta/list.html"

    def get(self, request):
        filial = _filial(request)
        qs = (
            OrdemColeta.objects.for_filial(filial)
            .select_related("cliente", "fornecedor", "transportadora", "romaneio", "responsavel")
            .annotate(qtd_itens=Count("itens"))
        )

        status = request.GET.get("status", "")
        q = request.GET.get("q", "").strip()
        data_ini = request.GET.get("data_ini", "")
        data_fim = request.GET.get("data_fim", "")

        if status:
            qs = qs.filter(status=status)
        if q:
            qs = qs.filter(
                Q(numero__icontains=q)
                | Q(solicitante_nome__icontains=q)
                | Q(contato_nome__icontains=q)
                | Q(contato_telefone__icontains=q)
                | Q(cliente__razao_social__icontains=q)
                | Q(fornecedor__razao_social__icontains=q)
                | Q(transportadora__razao_social__icontains=q)
            )
        if data_ini:
            qs = qs.filter(data_solicitacao__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_solicitacao__lte=data_fim)

        kpis = qs.aggregate(
            total=Count("id"),
            itens=Count("itens"),
            peso=Sum("peso_total_kg"),
            valor=Sum("valor_estimado"),
        )
        page_obj = Paginator(qs, 30).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "title": "Ordens de Coleta",
            "ordens": page_obj.object_list,
            "page_obj": page_obj,
            "status_choices": OrdemColeta.Status.choices,
            "status_filtro": status,
            "q": q,
            "data_ini": data_ini,
            "data_fim": data_fim,
            "kpis": kpis,
        })


class OrdemColetaCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "criar"
    template_name = "logistica/ordem_coleta/form.html"

    def get(self, request):
        filial = _filial(request)
        form = OrdemColetaForm(filial=filial, initial={
            "numero": _proximo_numero_ordem_coleta(filial),
            "data_solicitacao": timezone.localdate(),
        })
        clientes_json, fornecedores_json = _clientes_fornecedores_json(filial)
        return render(request, self.template_name, {
            "title": "Nova Ordem de Coleta",
            "form": form,
            "cancel_url": reverse("logistica:ordem-coleta-list"),
            "clientes_json": clientes_json,
            "fornecedores_json": fornecedores_json,
        })

    def post(self, request):
        filial = _filial(request)
        form = OrdemColetaForm(request.POST, filial=filial)
        if form.is_valid():
            ordem = form.save(commit=False)
            ordem.filial = filial
            ordem.responsavel = request.user
            ordem.save()
            messages.success(request, f"Ordem de Coleta #{ordem.numero:06d} criada.")
            return redirect("logistica:ordem-coleta-detail", pk=ordem.pk)
        clientes_json, fornecedores_json = _clientes_fornecedores_json(filial)
        return render(request, self.template_name, {
            "title": "Nova Ordem de Coleta",
            "form": form,
            "cancel_url": reverse("logistica:ordem-coleta-list"),
            "clientes_json": clientes_json,
            "fornecedores_json": fornecedores_json,
        })


class OrdemColetaUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"
    template_name = "logistica/ordem_coleta/form.html"

    def get(self, request, pk):
        filial = _filial(request)
        ordem = get_object_or_404(OrdemColeta.objects.for_filial(filial), pk=pk)
        form = OrdemColetaForm(instance=ordem, filial=filial)
        clientes_json, fornecedores_json = _clientes_fornecedores_json(filial)
        return render(request, self.template_name, {
            "title": f"Editar Ordem #{ordem.numero:06d}",
            "form": form,
            "ordem": ordem,
            "cancel_url": reverse("logistica:ordem-coleta-detail", kwargs={"pk": ordem.pk}),
            "clientes_json": clientes_json,
            "fornecedores_json": fornecedores_json,
        })

    def post(self, request, pk):
        filial = _filial(request)
        ordem = get_object_or_404(OrdemColeta.objects.for_filial(filial), pk=pk)
        form = OrdemColetaForm(request.POST, instance=ordem, filial=filial)
        if form.is_valid():
            form.save()
            messages.success(request, f"Ordem de Coleta #{ordem.numero:06d} atualizada.")
            return redirect("logistica:ordem-coleta-detail", pk=ordem.pk)
        clientes_json, fornecedores_json = _clientes_fornecedores_json(filial)
        return render(request, self.template_name, {
            "title": f"Editar Ordem #{ordem.numero:06d}",
            "form": form,
            "ordem": ordem,
            "cancel_url": reverse("logistica:ordem-coleta-detail", kwargs={"pk": ordem.pk}),
            "clientes_json": clientes_json,
            "fornecedores_json": fornecedores_json,
        })


class OrdemColetaDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    template_name = "logistica/ordem_coleta/detail.html"

    def get(self, request, pk):
        ordem = get_object_or_404(
            OrdemColeta.objects.for_filial(_filial(request)).select_related(
                "cliente", "fornecedor", "transportadora", "romaneio", "responsavel"
            ),
            pk=pk,
        )
        itens = ordem.itens.all()
        item_form = ItemOrdemColetaForm(initial={"quantidade": 1, "unidade": "UN"})
        return render(request, self.template_name, {
            "title": f"Ordem de Coleta #{ordem.numero:06d}",
            "ordem": ordem,
            "itens": itens,
            "item_form": item_form,
        })


class ItemOrdemColetaCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        ordem = get_object_or_404(OrdemColeta.objects.for_filial(_filial(request)), pk=pk)
        form = ItemOrdemColetaForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.ordem = ordem
            item.save()
            ordem.recalcular_totais()
            messages.success(request, "Item adicionado a ordem de coleta.")
        else:
            messages.error(request, "Revise os dados do item da coleta.")
        return redirect("logistica:ordem-coleta-detail", pk=ordem.pk)


class ItemOrdemColetaDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk, item_pk):
        ordem = get_object_or_404(OrdemColeta.objects.for_filial(_filial(request)), pk=pk)
        item = get_object_or_404(ItemOrdemColeta.objects.filter(ordem=ordem), pk=item_pk)
        item.delete()
        ordem.recalcular_totais()
        messages.success(request, "Item removido da ordem de coleta.")
        return redirect("logistica:ordem-coleta-detail", pk=ordem.pk)


class ManifestoCargaListView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    template_name = "logistica/manifesto/list.html"

    def get(self, request):
        filial = _filial(request)
        qs = (
            ManifestoCarga.objects.for_filial(filial)
            .select_related("transportadora", "romaneio", "responsavel")
            .annotate(documentos_count=Count("documentos"))
        )
        status = request.GET.get("status", "")
        q = request.GET.get("q", "").strip()
        data_ini = request.GET.get("data_ini", "")
        data_fim = request.GET.get("data_fim", "")

        if status:
            qs = qs.filter(status=status)
        if q:
            qs = qs.filter(
                Q(numero__icontains=q)
                | Q(motorista_nome__icontains=q)
                | Q(veiculo_placa__icontains=q)
                | Q(cidade_origem__icontains=q)
                | Q(cidade_destino__icontains=q)
                | Q(transportadora__razao_social__icontains=q)
                | Q(transportadora__nome_fantasia__icontains=q)
            )
        if data_ini:
            qs = qs.filter(data_emissao__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_emissao__lte=data_fim)

        kpis = qs.aggregate(
            total=Count("id"),
            documentos=Count("documentos"),
            peso=Sum("peso_total_kg"),
            valor=Sum("valor_total"),
        )
        page_obj = Paginator(qs, 30).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "title": "Manifestos de Carga",
            "manifestos": page_obj.object_list,
            "page_obj": page_obj,
            "status_choices": ManifestoCarga.Status.choices,
            "status_filtro": status,
            "q": q,
            "data_ini": data_ini,
            "data_fim": data_fim,
            "kpis": kpis,
        })


class ManifestoCargaCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "criar"
    template_name = "logistica/manifesto/form.html"

    def get(self, request):
        filial = _filial(request)
        form = ManifestoCargaForm(filial=filial, initial={
            "numero": _proximo_numero_manifesto(filial),
            "data_emissao": timezone.localdate(),
        })
        motoristas_json, veiculos_json = _motoristas_veiculos_json(filial)
        return render(request, self.template_name, {
            "title": "Novo Manifesto de Carga",
            "form": form,
            "cancel_url": reverse("logistica:manifesto-list"),
            "motoristas_json": motoristas_json,
            "veiculos_json": veiculos_json,
        })

    def post(self, request):
        filial = _filial(request)
        form = ManifestoCargaForm(request.POST, filial=filial)
        if form.is_valid():
            manifesto = form.save(commit=False)
            manifesto.filial = filial
            manifesto.responsavel = request.user
            manifesto.save()
            messages.success(request, f"Manifesto #{manifesto.numero:06d} criado.")
            return redirect("logistica:manifesto-detail", pk=manifesto.pk)
        motoristas_json, veiculos_json = _motoristas_veiculos_json(filial)
        return render(request, self.template_name, {
            "title": "Novo Manifesto de Carga",
            "form": form,
            "cancel_url": reverse("logistica:manifesto-list"),
            "motoristas_json": motoristas_json,
            "veiculos_json": veiculos_json,
        })


class ManifestoCargaUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"
    template_name = "logistica/manifesto/form.html"

    def get(self, request, pk):
        filial = _filial(request)
        manifesto = get_object_or_404(ManifestoCarga.objects.for_filial(filial), pk=pk)
        form = ManifestoCargaForm(instance=manifesto, filial=filial)
        motoristas_json, veiculos_json = _motoristas_veiculos_json(filial)
        return render(request, self.template_name, {
            "title": f"Editar Manifesto #{manifesto.numero:06d}",
            "form": form,
            "manifesto": manifesto,
            "cancel_url": reverse("logistica:manifesto-detail", kwargs={"pk": manifesto.pk}),
            "motoristas_json": motoristas_json,
            "veiculos_json": veiculos_json,
        })

    def post(self, request, pk):
        filial = _filial(request)
        manifesto = get_object_or_404(ManifestoCarga.objects.for_filial(filial), pk=pk)
        form = ManifestoCargaForm(request.POST, instance=manifesto, filial=filial)
        if form.is_valid():
            form.save()
            messages.success(request, f"Manifesto #{manifesto.numero:06d} atualizado.")
            return redirect("logistica:manifesto-detail", pk=manifesto.pk)
        motoristas_json, veiculos_json = _motoristas_veiculos_json(filial)
        return render(request, self.template_name, {
            "title": f"Editar Manifesto #{manifesto.numero:06d}",
            "form": form,
            "manifesto": manifesto,
            "cancel_url": reverse("logistica:manifesto-detail", kwargs={"pk": manifesto.pk}),
            "motoristas_json": motoristas_json,
            "veiculos_json": veiculos_json,
        })


class ManifestoCargaDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    template_name = "logistica/manifesto/detail.html"

    def get(self, request, pk):
        manifesto = get_object_or_404(
            ManifestoCarga.objects.for_filial(_filial(request)).select_related(
                "transportadora", "romaneio", "responsavel"
            ),
            pk=pk,
        )
        documentos = manifesto.documentos.all()
        documento_form = DocumentoManifestoCargaForm()
        return render(request, self.template_name, {
            "title": f"Manifesto #{manifesto.numero:06d}",
            "manifesto": manifesto,
            "documentos": documentos,
            "documento_form": documento_form,
        })


class DocumentoManifestoCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        manifesto = get_object_or_404(ManifestoCarga.objects.for_filial(_filial(request)), pk=pk)
        form = DocumentoManifestoCargaForm(request.POST)
        if form.is_valid():
            documento = form.save(commit=False)
            documento.manifesto = manifesto
            documento.save()
            manifesto.recalcular_totais()
            messages.success(request, "Documento adicionado ao manifesto.")
        else:
            messages.error(request, "Revise os dados do documento do manifesto.")
        return redirect("logistica:manifesto-detail", pk=manifesto.pk)


class DocumentoManifestoDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk, documento_pk):
        manifesto = get_object_or_404(ManifestoCarga.objects.for_filial(_filial(request)), pk=pk)
        documento = get_object_or_404(DocumentoManifestoCarga.objects.filter(manifesto=manifesto), pk=documento_pk)
        documento.delete()
        manifesto.recalcular_totais()
        messages.success(request, "Documento removido do manifesto.")
        return redirect("logistica:manifesto-detail", pk=manifesto.pk)


class CTeListView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    template_name = "logistica/cte/list.html"

    def get(self, request):
        filial = _filial(request)
        qs = (
            CTe.objects.for_filial(filial)
            .select_related("transportadora", "responsavel")
            .annotate(qtd_documentos=Count("documentos"))
        )

        status = request.GET.get("status", "")
        q = request.GET.get("q", "").strip()
        data_ini = request.GET.get("data_ini", "")
        data_fim = request.GET.get("data_fim", "")

        if status:
            qs = qs.filter(status=status)
        if q:
            qs = qs.filter(
                Q(numero__icontains=q)
                | Q(numero_cte__icontains=q)
                | Q(chave_acesso__icontains=q)
                | Q(remetente_nome__icontains=q)
                | Q(destinatario_nome__icontains=q)
                | Q(veiculo_placa__icontains=q)
                | Q(transportadora__razao_social__icontains=q)
                | Q(transportadora__nome_fantasia__icontains=q)
            )
        if data_ini:
            qs = qs.filter(data_emissao__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_emissao__lte=data_fim)

        kpis = qs.aggregate(
            total=Count("id"),
            documentos=Count("documentos"),
            peso=Sum("peso_total_kg"),
            valor=Sum("valor_frete"),
        )
        page_obj = Paginator(qs, 30).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "title": "CT-e",
            "ctes": page_obj.object_list,
            "page_obj": page_obj,
            "status_choices": CTe.Status.choices,
            "status_filtro": status,
            "q": q,
            "data_ini": data_ini,
            "data_fim": data_fim,
            "kpis": kpis,
        })


class CTeCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "criar"
    template_name = "logistica/cte/form.html"

    def get(self, request):
        filial = _filial(request)
        form = CTeForm(filial=filial, initial={
            "numero": _proximo_numero_cte(filial),
            "data_emissao": timezone.localdate(),
        })
        return render(request, self.template_name, {
            "title": "Novo CT-e",
            "form": form,
            "cancel_url": reverse("logistica:cte-list"),
        })

    def post(self, request):
        filial = _filial(request)
        form = CTeForm(request.POST, filial=filial)
        if form.is_valid():
            cte = form.save(commit=False)
            cte.filial = filial
            cte.responsavel = request.user
            cte.valor_total = (
                (cte.valor_frete or 0) + (cte.valor_pedagio or 0) + (cte.valor_outros or 0)
            )
            cte.save()
            messages.success(request, f"CT-e #{cte.numero:06d} criado.")
            return redirect("logistica:cte-detail", pk=cte.pk)
        return render(request, self.template_name, {
            "title": "Novo CT-e",
            "form": form,
            "cancel_url": reverse("logistica:cte-list"),
        })


class CTeUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"
    template_name = "logistica/cte/form.html"

    def get(self, request, pk):
        cte = get_object_or_404(CTe.objects.for_filial(_filial(request)), pk=pk)
        form = CTeForm(instance=cte, filial=_filial(request))
        return render(request, self.template_name, {
            "title": f"Editar CT-e #{cte.numero:06d}",
            "form": form,
            "cte": cte,
            "cancel_url": reverse("logistica:cte-detail", kwargs={"pk": cte.pk}),
        })

    def post(self, request, pk):
        cte = get_object_or_404(CTe.objects.for_filial(_filial(request)), pk=pk)
        form = CTeForm(request.POST, instance=cte, filial=_filial(request))
        if form.is_valid():
            obj = form.save(commit=False)
            obj.valor_total = (
                (obj.valor_frete or 0) + (obj.valor_pedagio or 0) + (obj.valor_outros or 0)
            )
            obj.save()
            messages.success(request, f"CT-e #{cte.numero:06d} atualizado.")
            return redirect("logistica:cte-detail", pk=cte.pk)
        return render(request, self.template_name, {
            "title": f"Editar CT-e #{cte.numero:06d}",
            "form": form,
            "cte": cte,
            "cancel_url": reverse("logistica:cte-detail", kwargs={"pk": cte.pk}),
        })


class CTeDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    template_name = "logistica/cte/detail.html"

    def get(self, request, pk):
        cte = get_object_or_404(
            CTe.objects.for_filial(_filial(request)).select_related("transportadora", "responsavel"),
            pk=pk,
        )
        documentos = cte.documentos.all()
        documento_form = DocumentoCTeForm()
        doc_fiscal = DocumentoFiscal.objects.filter(origem_tipo="cte", origem_id=cte.pk).first()
        return render(request, self.template_name, {
            "title": f"CT-e #{cte.numero:06d}",
            "cte": cte,
            "documentos": documentos,
            "documento_form": documento_form,
            "doc_fiscal": doc_fiscal,
        })


class DocumentoCTeCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        cte = get_object_or_404(CTe.objects.for_filial(_filial(request)), pk=pk)
        form = DocumentoCTeForm(request.POST)
        if form.is_valid():
            documento = form.save(commit=False)
            documento.cte = cte
            documento.save()
            cte.recalcular_totais()
            messages.success(request, "Documento adicionado ao CT-e.")
        else:
            messages.error(request, "Revise os dados do documento do CT-e.")
        return redirect("logistica:cte-detail", pk=cte.pk)


class DocumentoCTeDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk, documento_pk):
        cte = get_object_or_404(CTe.objects.for_filial(_filial(request)), pk=pk)
        documento = get_object_or_404(DocumentoCTe.objects.filter(cte=cte), pk=documento_pk)
        documento.delete()
        cte.recalcular_totais()
        messages.success(request, "Documento removido do CT-e.")
        return redirect("logistica:cte-detail", pk=cte.pk)


# --------------------------------------------------------------------------
# CT-e Focus NFe — Emissao Fiscal
# --------------------------------------------------------------------------

class CTeEmitirView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        from apps.logistica.services.cte_focusnfe import emitir_cte
        cte = get_object_or_404(CTe.objects.for_filial(_filial(request)), pk=pk)
        doc, erro = emitir_cte(cte, request.user)
        if erro:
            messages.error(request, f"Erro ao emitir CT-e: {erro}")
        else:
            messages.success(request, "CT-e enviado para autorizacao na SEFAZ.")
        return redirect("logistica:cte-detail", pk=cte.pk)


class CTeConsultarView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        from apps.logistica.services.cte_focusnfe import consultar_cte
        cte = get_object_or_404(CTe.objects.for_filial(_filial(request)), pk=pk)
        doc, erro = consultar_cte(cte)
        if erro:
            messages.error(request, f"Erro ao consultar CT-e: {erro}")
        else:
            messages.success(request, f"Status atualizado: {doc.get_status_display() if doc else ''}.")
        return redirect("logistica:cte-detail", pk=cte.pk)


class CteCancelarView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        from apps.logistica.services.cte_focusnfe import cancelar_cte
        cte = get_object_or_404(CTe.objects.for_filial(_filial(request)), pk=pk)
        justificativa = request.POST.get("justificativa", "").strip()
        if len(justificativa) < 15:
            messages.error(request, "Justificativa deve ter no minimo 15 caracteres.")
            return redirect("logistica:cte-detail", pk=cte.pk)
        doc, erro = cancelar_cte(cte, justificativa, usuario=request.user)
        if erro:
            messages.error(request, f"Erro ao cancelar CT-e: {erro}")
        else:
            messages.success(request, "CT-e cancelado com sucesso.")
        return redirect("logistica:cte-detail", pk=cte.pk)


class CteDACTEView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"

    def get(self, request, pk):
        from apps.logistica.services.cte_focusnfe import dacte_pdf
        cte = get_object_or_404(CTe.objects.for_filial(_filial(request)), pk=pk)
        try:
            pdf_bytes = dacte_pdf(cte)
        except Exception as exc:
            messages.error(request, f"Erro ao baixar DACTE: {exc}")
            return redirect("logistica:cte-detail", pk=cte.pk)
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="dacte-{cte.numero:06d}.pdf"'
        return resp


# ── OMS — Pedidos de Expedição ───────────────────────────────────────────────

def _proximo_numero_pedido_expedicao(filial):
    ultimo = (
        PedidoExpedicao.objects.for_filial(filial)
        .order_by("-numero")
        .values_list("numero", flat=True)
        .first()
    )
    return (ultimo or 0) + 1


class PedidoExpedicaoListView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    template_name = "logistica/pedido_expedicao/list.html"

    def get(self, request):
        filial = _filial(request)
        qs = (
            PedidoExpedicao.objects.for_filial(filial)
            .select_related("cliente", "transportadora", "responsavel")
            .annotate(qtd_itens=Count("itens"))
        )

        status = request.GET.get("status", "")
        prioridade = request.GET.get("prioridade", "")
        q = request.GET.get("q", "").strip()
        data_ini = request.GET.get("data_ini", "")
        data_fim = request.GET.get("data_fim", "")

        if status:
            qs = qs.filter(status=status)
        if prioridade:
            qs = qs.filter(prioridade=prioridade)
        if q:
            qs = qs.filter(
                Q(numero__icontains=q)
                | Q(cliente__razao_social__icontains=q)
                | Q(cliente__nome_fantasia__icontains=q)
                | Q(motorista_nome__icontains=q)
                | Q(veiculo_placa__icontains=q)
            )
        if data_ini:
            qs = qs.filter(data_pedido__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_pedido__lte=data_fim)

        kpis = qs.aggregate(
            total=Count("id"),
            abertos=Count("id", filter=Q(status=PedidoExpedicao.Status.ABERTO)),
            em_separacao=Count("id", filter=Q(status=PedidoExpedicao.Status.EM_SEPARACAO)),
            expedidos=Count("id", filter=Q(status=PedidoExpedicao.Status.EXPEDIDO)),
            valor=Sum("valor_total"),
        )
        page_obj = Paginator(qs, 30).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "title": "Pedidos de Expedição (OMS)",
            "pedidos": page_obj.object_list,
            "page_obj": page_obj,
            "status_choices": PedidoExpedicao.Status.choices,
            "prioridade_choices": PedidoExpedicao.Prioridade.choices,
            "status_filtro": status,
            "prioridade_filtro": prioridade,
            "q": q,
            "data_ini": data_ini,
            "data_fim": data_fim,
            "kpis": kpis,
        })


class PedidoExpedicaoCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "criar"
    template_name = "logistica/pedido_expedicao/form.html"

    def get(self, request):
        filial = _filial(request)
        clientes_json, _ = _clientes_fornecedores_json(filial)
        form = PedidoExpedicaoForm(filial=filial, initial={
            "numero": _proximo_numero_pedido_expedicao(filial),
            "data_pedido": timezone.localdate(),
        })
        return render(request, self.template_name, {
            "title": "Novo Pedido de Expedição",
            "form": form,
            "cancel_url": reverse("logistica:pedido-expedicao-list"),
            "clientes_json": clientes_json,
        })

    def post(self, request):
        filial = _filial(request)
        form = PedidoExpedicaoForm(request.POST, filial=filial)
        if form.is_valid():
            pedido = form.save(commit=False)
            pedido.filial = filial
            pedido.responsavel = request.user
            pedido.save()
            messages.success(request, f"Pedido #{pedido.numero:06d} criado.")
            return redirect("logistica:pedido-expedicao-detail", pk=pedido.pk)
        clientes_json, _ = _clientes_fornecedores_json(filial)
        return render(request, self.template_name, {
            "title": "Novo Pedido de Expedição",
            "form": form,
            "cancel_url": reverse("logistica:pedido-expedicao-list"),
            "clientes_json": clientes_json,
        })


class PedidoExpedicaoUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"
    template_name = "logistica/pedido_expedicao/form.html"

    def get(self, request, pk):
        filial = _filial(request)
        pedido = get_object_or_404(PedidoExpedicao.objects.for_filial(filial), pk=pk)
        form = PedidoExpedicaoForm(instance=pedido, filial=filial)
        clientes_json, _ = _clientes_fornecedores_json(filial)
        return render(request, self.template_name, {
            "title": f"Editar Pedido #{pedido.numero:06d}",
            "form": form,
            "pedido": pedido,
            "cancel_url": reverse("logistica:pedido-expedicao-detail", kwargs={"pk": pedido.pk}),
            "clientes_json": clientes_json,
        })

    def post(self, request, pk):
        filial = _filial(request)
        pedido = get_object_or_404(PedidoExpedicao.objects.for_filial(filial), pk=pk)
        form = PedidoExpedicaoForm(request.POST, instance=pedido, filial=filial)
        if form.is_valid():
            form.save()
            messages.success(request, f"Pedido #{pedido.numero:06d} atualizado.")
            return redirect("logistica:pedido-expedicao-detail", pk=pedido.pk)
        clientes_json, _ = _clientes_fornecedores_json(filial)
        return render(request, self.template_name, {
            "title": f"Editar Pedido #{pedido.numero:06d}",
            "form": form,
            "pedido": pedido,
            "cancel_url": reverse("logistica:pedido-expedicao-detail", kwargs={"pk": pedido.pk}),
            "clientes_json": clientes_json,
        })


class PedidoExpedicaoDeleteView(PermissaoRequiredMixin, View):
    """Exclui um pedido de expedição. Os itens vão junto (FK em cascata)."""

    permissao_modulo = "logistica"
    permissao_acao = "excluir"

    # Depois de expedido o pedido virou historico de operacao: o que saiu,
    # quando e para quem. Apagar isso deixaria o registro da entrega sem
    # origem, entao a saida aqui e' cancelar, nao excluir.
    STATUS_BLOQUEADOS = (
        PedidoExpedicao.Status.EXPEDIDO,
        PedidoExpedicao.Status.ENTREGUE,
    )

    def post(self, request, pk):
        pedido = get_object_or_404(
            PedidoExpedicao.objects.for_filial(_filial(request)), pk=pk
        )

        if pedido.status in self.STATUS_BLOQUEADOS:
            messages.error(
                request,
                f"Pedido #{pedido.numero:06d} já foi {pedido.get_status_display().lower()} "
                f"e não pode ser excluído. Use o cancelamento para encerrá-lo sem perder o histórico.",
            )
            return redirect("logistica:pedido-expedicao-list")

        numero = pedido.numero
        # Avisa que a exclusao mexeu num romaneio ja montado -- sem isso o
        # pedido simplesmente sumiria da carga sem ninguem perceber.
        romaneio = pedido.romaneio
        pedido.delete()

        if romaneio:
            messages.success(
                request,
                f"Pedido #{numero:06d} excluído. Ele saiu do romaneio {romaneio}, "
                f"que agora tem uma carga a menos.",
            )
        else:
            messages.success(request, f"Pedido #{numero:06d} excluído.")
        return redirect("logistica:pedido-expedicao-list")


class PedidoExpedicaoDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    template_name = "logistica/pedido_expedicao/detail.html"

    def get(self, request, pk):
        pedido = get_object_or_404(
            PedidoExpedicao.objects.for_filial(_filial(request))
            .select_related("cliente", "transportadora", "romaneio", "responsavel"),
            pk=pk,
        )
        itens = pedido.itens.all()
        item_form = ItemPedidoExpedicaoForm(initial={"quantidade": 1, "unidade": "UN"})
        return render(request, self.template_name, {
            "title": f"Pedido #{pedido.numero:06d}",
            "pedido": pedido,
            "itens": itens,
            "item_form": item_form,
        })


class ItemPedidoExpedicaoCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        pedido = get_object_or_404(PedidoExpedicao.objects.for_filial(_filial(request)), pk=pk)
        form = ItemPedidoExpedicaoForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.pedido = pedido
            item.ordem = pedido.itens.count() + 1
            item.save()
            pedido.recalcular_totais()
            messages.success(request, "Item adicionado ao pedido.")
        else:
            messages.error(request, "Revise os dados do item.")
        return redirect("logistica:pedido-expedicao-detail", pk=pedido.pk)


class ItemPedidoExpedicaoDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk, item_pk):
        pedido = get_object_or_404(PedidoExpedicao.objects.for_filial(_filial(request)), pk=pk)
        item = get_object_or_404(ItemPedidoExpedicao.objects.filter(pedido=pedido), pk=item_pk)
        item.delete()
        pedido.recalcular_totais()
        messages.success(request, "Item removido do pedido.")
        return redirect("logistica:pedido-expedicao-detail", pk=pedido.pk)


class ItemPedidoExpedicaoToggleStatusView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk, item_pk):
        pedido = get_object_or_404(PedidoExpedicao.objects.for_filial(_filial(request)), pk=pk)
        item = get_object_or_404(ItemPedidoExpedicao.objects.filter(pedido=pedido), pk=item_pk)
        ciclo = [
            ItemPedidoExpedicao.StatusItem.PENDENTE,
            ItemPedidoExpedicao.StatusItem.SEPARADO,
            ItemPedidoExpedicao.StatusItem.EXPEDIDO,
        ]
        try:
            idx = ciclo.index(item.status_item)
            item.status_item = ciclo[(idx + 1) % len(ciclo)]
        except ValueError:
            item.status_item = ItemPedidoExpedicao.StatusItem.PENDENTE
        item.save(update_fields=["status_item", "updated_at"])
        return JsonResponse({"status": item.status_item, "label": item.get_status_item_display()})


# ── MDF-e ────────────────────────────────────────────────────────────────────

def _proximo_numero_mdfe(filial):
    from apps.core.models.parametros import ParametroDocumentoFiscal, ParametrosSistema

    parametros = ParametrosSistema.objects.filter(filial=filial).first()
    if parametros:
        configuracao = ParametroDocumentoFiscal.objects.filter(
            parametros=parametros,
            tipo_documento=ParametroDocumentoFiscal.TipoDocumento.MDFE,
        ).first()
        if configuracao:
            serie = str(configuracao.serie or 1)
            ultimo = (
                MDFe.objects.for_filial(filial)
                .filter(serie=serie)
                .order_by("-numero")
                .values_list("numero", flat=True)
                .first()
            ) or 0
            return max(configuracao.proximo_numero, ultimo + 1), serie
    ultimo = (
        MDFe.objects.for_filial(filial)
        .filter(serie="1")
        .order_by("-numero")
        .values_list("numero", flat=True)
        .first()
    ) or 0
    return ultimo + 1, "1"


def _peso_bruto_nfe(documento):
    """Obtém o peso da NF-e; usa o XML e recorre ao cadastro dos produtos."""
    for xml in (documento.xml_assinado, documento.xml_retorno, documento.xml_enviado):
        if not xml:
            continue
        try:
            raiz = ElementTree.fromstring(xml)
            pesos = raiz.findall(".//{*}transp/{*}vol/{*}pesoB")
            total = sum(
                (Decimal((peso.text or "0").replace(",", ".")) for peso in pesos),
                Decimal("0"),
            )
            if total > 0:
                return total
        except (ElementTree.ParseError, InvalidOperation):
            continue

    total = Decimal("0")
    for item in documento.itens.select_related("produto"):
        peso_unitario = getattr(item.produto, "peso_bruto", None) if item.produto else None
        if peso_unitario:
            total += Decimal(str(peso_unitario)) * item.quantidade
    return total


def _peso_produtos_nfe(documento):
    """Calcula o peso pelos produtos e informa quais cadastros estão incompletos."""
    total = Decimal("0")
    produtos_sem_peso = []
    for item in documento.itens.select_related("produto"):
        produto = item.produto
        peso_unitario = getattr(produto, "peso_bruto", None) if produto else None
        if not peso_unitario or peso_unitario <= 0:
            produtos_sem_peso.append(
                getattr(produto, "nome", "") or item.descricao or item.codigo_produto
            )
            continue
        total += Decimal(str(peso_unitario)) * item.quantidade
    return total, list(dict.fromkeys(produtos_sem_peso))


def _texto_xml_destino(documento):
    for xml in (documento.xml_assinado, documento.xml_retorno, documento.xml_enviado):
        if not xml:
            continue
        try:
            raiz = ElementTree.fromstring(xml)
        except ElementTree.ParseError:
            continue
        endereco = raiz.find(".//{*}dest/{*}enderDest")
        if endereco is None:
            continue

        def texto(tag):
            elemento = endereco.find(f"{{*}}{tag}")
            return (elemento.text or "").strip() if elemento is not None else ""

        return {
            "logradouro": texto("xLgr"),
            "numero": texto("nro"),
            "complemento": texto("xCpl"),
            "bairro": texto("xBairro"),
            "cidade": texto("xMun"),
            "uf": texto("UF"),
            "cep": texto("CEP"),
            "codigo_municipio": texto("cMun"),
        }
    return {}


def _dados_destino_nfe(documento):
    snapshot = dict(documento.destinatario_snapshot or {})
    filial_destino = _filial_destino_nfe(documento, snapshot)
    xml = _texto_xml_destino(documento)

    def primeiro(*valores):
        return next((str(valor).strip() for valor in valores if valor), "")

    dados = {
        "nome": primeiro(snapshot.get("nome"), getattr(filial_destino, "razao_social", "")),
        "logradouro": primeiro(
            getattr(filial_destino, "endereco", ""),
            snapshot.get("logradouro"), snapshot.get("endereco"),
            xml.get("logradouro"),
        ),
        "numero": primeiro(
            getattr(filial_destino, "numero", ""), snapshot.get("numero"),
            xml.get("numero"),
        ),
        "complemento": primeiro(
            getattr(filial_destino, "complemento", ""), snapshot.get("complemento"),
            xml.get("complemento"),
        ),
        "bairro": primeiro(
            getattr(filial_destino, "bairro", ""), snapshot.get("bairro"),
            xml.get("bairro"),
        ),
        "cidade": primeiro(
            getattr(filial_destino, "cidade", ""),
            snapshot.get("cidade"), snapshot.get("municipio"),
            snapshot.get("nome_municipio"),
            xml.get("cidade"),
        ),
        "uf": primeiro(
            getattr(filial_destino, "uf", ""), snapshot.get("uf"), xml.get("uf"),
        ).upper(),
        "cep": primeiro(
            getattr(filial_destino, "cep", ""), snapshot.get("cep"), xml.get("cep"),
        ),
        "codigo_municipio": primeiro(
            getattr(filial_destino, "codigo_municipio_ibge", ""),
            snapshot.get("codigo_municipio"),
            snapshot.get("codigo_municipio_ibge"),
            snapshot.get("codigo_ibge"),
            xml.get("codigo_municipio"),
        ),
    }
    partes = [
        " ".join(filter(None, [dados["logradouro"], dados["numero"]])),
        dados["complemento"],
        dados["bairro"],
        " / ".join(filter(None, [dados["cidade"], dados["uf"]])),
        dados["cep"],
    ]
    dados["endereco_completo"] = " - ".join(parte for parte in partes if parte)
    return dados


def _filial_destino_nfe(documento, snapshot=None):
    """Resolve a filial destinataria sem depender de snapshots antigos."""
    snapshot = snapshot or dict(documento.destinatario_snapshot or {})
    if (
        getattr(documento, "destinatario_tipo", "") == "filial"
        and getattr(documento, "destinatario_id", None)
    ):
        filial = Filial.objects.filter(pk=documento.destinatario_id).first()
        if filial:
            return filial

    if getattr(documento, "pk", None):
        movimento = (
            MovimentacaoEstoque.objects
            .filter(
                documento_fiscal=documento,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
            )
            .select_related("filial_destino")
            .first()
        )
        if movimento and movimento.filial_destino_id:
            return movimento.filial_destino

    documento_destino = "".join(
        ch for ch in str(
            snapshot.get("cpf_cnpj")
            or snapshot.get("cnpj")
            or snapshot.get("documento")
            or ""
        )
        if ch.isdigit()
    )
    if documento_destino:
        return Filial.objects.filter(cnpj=documento_destino).first()
    return None


def _endereco_filial(filial):
    partes = [
        " ".join(filter(None, [filial.endereco, filial.numero])),
        filial.complemento,
        filial.bairro,
        " / ".join(filter(None, [filial.cidade, filial.uf])),
        filial.cep,
    ]
    return " - ".join(parte for parte in partes if parte)


def _endereco_destino_nfe(documento):
    """
    Endereço de descarregamento, venha ele de uma filial ou de um cliente.

    Antes só existia o caminho da filial: numa entrega para CLIENTE o campo
    ficava vazio e a tela dizia "Endereço não cadastrado na filial de destino"
    — mensagem que manda conferir um cadastro que não é o do problema.

    Os dados do cliente já estão no snapshot do destinatário da NF-e, que é a
    mesma fonte usada para o município de descarregamento. Só não estavam
    sendo usados aqui.
    """
    destino = _filial_destino_nfe(documento)
    if destino:
        return _endereco_filial(destino)

    d = _dados_destino_nfe(documento)
    partes = [
        " ".join(filter(None, [d.get("logradouro"), d.get("numero")])),
        d.get("complemento"),
        d.get("bairro"),
        " / ".join(filter(None, [d.get("cidade"), d.get("uf")])),
        d.get("cep"),
    ]
    return " - ".join(parte for parte in partes if parte)


def _rota_filiais_nfe(documento):
    """Monta a rota usando filiais e o XML autorizado como contingencia."""
    origem = documento.filial
    destino = _filial_destino_nfe(documento)
    dados_destino = _dados_destino_nfe(documento)
    return {
        "origem": origem,
        "destino": destino,
        "uf_carregamento": origem.uf or "",
        "municipio_carregamento": origem.cidade or "",
        "codigo_municipio_carregamento": origem.codigo_municipio_ibge or "",
        "uf_descarregamento": dados_destino.get("uf", ""),
        "municipio_descarregamento": dados_destino.get("cidade", ""),
        "codigo_municipio_descarregamento": dados_destino.get(
            "codigo_municipio", ""
        ),
    }


def _nfe_inicial(documento):
    if not documento:
        return None
    destinatario = _dados_destino_nfe(documento)
    peso = _peso_bruto_nfe(documento)
    return {
        "id": documento.pk,
        "label": f"NF-e nº {documento.numero} · Série {documento.serie}",
        "numero": documento.numero,
        "serie": documento.serie,
        "chave": documento.chave or "",
        "valor_total": str(documento.valor_total),
        "peso_kg": str(peso),
        "destinatario": destinatario.get("nome", ""),
        "municipio": destinatario.get("cidade", ""),
        "uf": destinatario.get("uf", ""),
        "codigo_municipio": destinatario.get("codigo_municipio", ""),
        "endereco_origem": _endereco_filial(documento.filial),
        "endereco_destino": destinatario.get("endereco_completo", ""),
    }


STATUS_MDFE_EDITAVEIS = {
    MDFe.Status.RASCUNHO,
    MDFe.Status.AGUARDANDO_NFE,
    MDFe.Status.REJEITADO,
}


def _vincular_nfe_ao_mdfe(mdfe, nfe_documento, atualizar_rota=True):
    if nfe_documento.filial_id != mdfe.filial_id:
        raise ValueError("A NF-e pertence a outra filial.")
    if (
        nfe_documento.tipo_documento != "nfe"
        or nfe_documento.status != "autorizada"
    ):
        raise ValueError("Selecione uma NF-e autorizada.")
    if DocumentoMDFe.objects.filter(
        documento_fiscal=nfe_documento
    ).exclude(mdfe=mdfe).exists():
        raise ValueError("Esta NF-e ja esta vinculada a outro MDF-e.")

    if atualizar_rota:
        rota = _rota_filiais_nfe(nfe_documento)
        campos_rota = {
            campo: valor
            for campo, valor in rota.items()
            if campo not in {"origem", "destino"}
        }
        for campo, valor in campos_rota.items():
            setattr(mdfe, campo, valor)
        mdfe.save(update_fields=[*campos_rota.keys(), "updated_at"])

    destinatario = _dados_destino_nfe(nfe_documento)
    peso_produtos, produtos_sem_peso = _peso_produtos_nfe(nfe_documento)
    peso_xml = _peso_bruto_nfe(nfe_documento)
    peso_documento = (
        peso_produtos
        if peso_produtos > 0 and not produtos_sem_peso
        else peso_xml or mdfe.peso_total_kg
    )
    DocumentoMDFe.objects.update_or_create(
        mdfe=mdfe,
        documento_fiscal=nfe_documento,
        defaults={
            "tipo_documento": DocumentoMDFe.TipoDocumento.NFE,
            "chave_acesso": nfe_documento.chave or "",
            "numero_documento": str(nfe_documento.numero),
            "serie": str(nfe_documento.serie),
            "emitente_nome": mdfe.filial.razao_social or "",
            "emitente_documento": mdfe.filial.cnpj or "",
            "municipio_descarga": destinatario.get("cidade", ""),
            "uf_descarga": destinatario.get("uf", ""),
            "peso_kg": peso_documento or 0,
            "valor": nfe_documento.valor_total or 0,
        },
    )
    mdfe.recalcular_totais()
    return mdfe


def _completar_rota_mdfe(mdfe):
    """Repara a rota com os dados da filial e da NF-e vinculada."""
    campos = {
        "uf_carregamento": mdfe.filial.uf or "",
        "municipio_carregamento": mdfe.filial.cidade or "",
        "codigo_municipio_carregamento": (
            mdfe.filial.codigo_municipio_ibge or ""
        ),
    }
    vinculo_nfe = (
        mdfe.documentos.select_related("documento_fiscal")
        .filter(tipo_documento=DocumentoMDFe.TipoDocumento.NFE)
        .first()
    )
    if vinculo_nfe and vinculo_nfe.documento_fiscal:
        rota = _rota_filiais_nfe(vinculo_nfe.documento_fiscal)
        campos.update({
            "uf_descarregamento": rota["uf_descarregamento"],
            "municipio_descarregamento": rota["municipio_descarregamento"],
            "codigo_municipio_descarregamento": (
                rota["codigo_municipio_descarregamento"]
            ),
        })
    elif vinculo_nfe:
        campos.update({
            "uf_descarregamento": vinculo_nfe.uf_descarga or "",
            "municipio_descarregamento": vinculo_nfe.municipio_descarga or "",
        })

    alterados = []
    for campo, valor in campos.items():
        valor = str(valor or "").strip()
        if valor and getattr(mdfe, campo) != valor:
            setattr(mdfe, campo, valor)
            alterados.append(campo)
    if alterados:
        mdfe.save(update_fields=[*alterados, "updated_at"])
    return mdfe


def _vincular_cte_ao_mdfe(mdfe, cte):
    if cte.filial_id != mdfe.filial_id:
        raise ValueError("O CT-e pertence a outra filial.")
    if cte.status != CTe.Status.AUTORIZADO:
        raise ValueError("Selecione um CT-e autorizado.")

    duplicado = DocumentoMDFe.objects.filter(
        tipo_documento=DocumentoMDFe.TipoDocumento.CTE,
    ).exclude(mdfe=mdfe)
    if cte.chave_acesso:
        duplicado = duplicado.filter(chave_acesso=cte.chave_acesso)
    else:
        duplicado = duplicado.filter(
            numero_documento=str(cte.numero),
            serie=str(cte.serie),
        )
    if duplicado.exists():
        raise ValueError("Este CT-e ja esta vinculado a outro MDF-e.")

    transportadora = cte.transportadora
    emitente_nome = (
        getattr(transportadora, "razao_social", "")
        or getattr(transportadora, "nome_fantasia", "")
        or cte.remetente_nome
        or ""
    )
    emitente_documento = (
        getattr(transportadora, "cpf_cnpj", "")
        or getattr(transportadora, "cnpj", "")
        or cte.remetente_documento
        or ""
    )
    DocumentoMDFe.objects.update_or_create(
        mdfe=mdfe,
        tipo_documento=DocumentoMDFe.TipoDocumento.CTE,
        numero_documento=str(cte.numero),
        serie=str(cte.serie),
        defaults={
            "chave_acesso": cte.chave_acesso or "",
            "emitente_nome": emitente_nome,
            "emitente_documento": emitente_documento,
            "municipio_descarga": cte.cidade_destino or "",
            "uf_descarga": cte.uf_destino or "",
            "peso_kg": cte.peso_total_kg or 0,
            "valor": cte.valor_carga or cte.valor_total or 0,
        },
    )
    mdfe.recalcular_totais()
    return mdfe


def _documentos_disponiveis_mdfe(mdfe):
    nfe_qs = (
        DocumentoFiscal.objects.filter(
            filial=mdfe.filial,
            tipo_documento="nfe",
            status="autorizada",
            vinculos_mdfe__isnull=True,
        )
        .distinct()
        .order_by("-data_emissao", "-numero")[:100]
    )
    documentos = []
    for nfe in nfe_qs:
        destinatario = nfe.destinatario_snapshot or {}
        documentos.append({
            "valor_selecao": f"nfe:{nfe.pk}",
            "tipo": "nfe",
            "tipo_label": "NF-e",
            "numero": nfe.numero,
            "serie": nfe.serie,
            "data_emissao": nfe.data_emissao,
            "parte": (
                destinatario.get("nome")
                or destinatario.get("razao_social")
                or destinatario.get("nome_fantasia")
                or "Destinatario nao informado"
            ),
            "chave": nfe.chave or "",
            "valor": nfe.valor_total or 0,
        })

    cte_qs = (
        CTe.objects.for_filial(mdfe.filial)
        .filter(status=CTe.Status.AUTORIZADO)
        .select_related("transportadora")
        .order_by("-data_emissao", "-numero")[:100]
    )
    for cte in cte_qs:
        identificador = Q(
            tipo_documento=DocumentoMDFe.TipoDocumento.CTE,
            numero_documento=str(cte.numero),
            serie=str(cte.serie),
        )
        if cte.chave_acesso:
            identificador = Q(chave_acesso=cte.chave_acesso)
        if DocumentoMDFe.objects.filter(identificador).exclude(mdfe=mdfe).exists():
            continue
        if DocumentoMDFe.objects.filter(mdfe=mdfe).filter(identificador).exists():
            continue
        documentos.append({
            "valor_selecao": f"cte:{cte.pk}",
            "tipo": "cte",
            "tipo_label": "CT-e",
            "numero": cte.numero,
            "serie": cte.serie,
            "data_emissao": cte.data_emissao,
            "parte": cte.destinatario_nome or cte.remetente_nome or "Participante nao informado",
            "chave": cte.chave_acesso or "",
            "valor": cte.valor_carga or cte.valor_total or 0,
        })
    return sorted(
        documentos,
        key=lambda item: (str(item["data_emissao"]), item["numero"]),
        reverse=True,
    )


class MDFeListView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    template_name = "logistica/mdfe/list.html"

    def get(self, request):
        from apps.logistica.services.mdfe_focusnfe import sincronizar_mdfe

        filial = _filial(request)
        mdfes_com_documento = (
            MDFe.objects.for_filial(filial)
            .exclude(documento_fiscal__isnull=True)
            .select_related("documento_fiscal")
        )
        for mdfe in mdfes_com_documento:
            sincronizar_mdfe(mdfe)

        qs = (
            MDFe.objects.for_filial(filial)
            .select_related(
                "transportadora", "responsavel", "romaneio", "documento_fiscal"
            )
            .annotate(qtd_documentos=Count("documentos"))
        )

        status = request.GET.get("status", "")
        q = request.GET.get("q", "").strip()
        data_ini = request.GET.get("data_ini", "")
        data_fim = request.GET.get("data_fim", "")

        if status:
            qs = qs.filter(status=status)
        if q:
            qs = qs.filter(
                Q(numero__icontains=q)
                | Q(chave_acesso__icontains=q)
                | Q(motorista_nome__icontains=q)
                | Q(veiculo_placa__icontains=q)
                | Q(transportadora__razao_social__icontains=q)
                | Q(transportadora__nome_fantasia__icontains=q)
            )
        if data_ini:
            qs = qs.filter(data_emissao__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_emissao__lte=data_fim)

        kpis = qs.aggregate(
            total=Count("id"),
            documentos=Count("documentos"),
            peso=Sum("peso_total_kg"),
            valor=Sum("valor_total"),
        )
        page_obj = Paginator(qs, 30).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "title": "MDF-e",
            "mdfes": page_obj.object_list,
            "page_obj": page_obj,
            "status_choices": MDFe.Status.choices,
            "status_filtro": status,
            "q": q,
            "data_ini": data_ini,
            "data_fim": data_fim,
            "kpis": kpis,
        })


class MDFeCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "criar"
    template_name = "logistica/mdfe/form.html"

    def get(self, request):
        filial = _filial(request)
        nfe_documento_id = request.GET.get("nfe_documento_id", "").strip()
        nfe_documento = None
        if nfe_documento_id:
            nfe_documento = DocumentoFiscal.objects.filter(
                pk=nfe_documento_id, filial=filial,
                tipo_documento="nfe", status="autorizada",
            ).first()
        proximo_numero, serie_mdfe = _proximo_numero_mdfe(filial)
        initial = {
            "numero": proximo_numero,
            "serie": serie_mdfe,
            "data_emissao": timezone.localdate(),
            "uf_carregamento": filial.uf,
            "municipio_carregamento": filial.cidade,
            "codigo_municipio_carregamento": filial.codigo_municipio_ibge,
            "inicio_viagem": timezone.localtime().replace(second=0, microsecond=0),
            "previsao_chegada": (
                timezone.localtime().replace(second=0, microsecond=0)
                + timedelta(hours=1)
            ),
        }
        nfe_documento_inicial_json = "null"
        rota_automatica = None
        produtos_sem_peso = []
        if nfe_documento:
            rota_automatica = _rota_filiais_nfe(nfe_documento)
            initial.update({
                campo: valor
                for campo, valor in rota_automatica.items()
                if campo not in {"origem", "destino"}
            })
            peso_carga, produtos_sem_peso = _peso_produtos_nfe(nfe_documento)
            if not produtos_sem_peso:
                initial["peso_carga_kg"] = peso_carga
            if nfe_documento.transportadora_id:
                initial["transportadora"] = nfe_documento.transportadora_id
            nfe_documento_inicial_json = json.dumps(
                _nfe_inicial(nfe_documento), ensure_ascii=False
            )

        motoristas = Motorista.objects.for_filial(filial).filter(ativo=True)
        veiculos = Veiculo.objects.for_filial(filial).filter(ativo=True)
        if motoristas.count() == 1:
            initial["motorista_cadastro"] = motoristas.values_list("pk", flat=True).first()
        if veiculos.count() == 1:
            initial["veiculo_cadastro"] = veiculos.values_list("pk", flat=True).first()
        form = MDFeForm(filial=filial, initial=initial)
        if nfe_documento:
            form.fields["peso_carga_kg"].widget.attrs["readonly"] = True
        motoristas_json, veiculos_json = _motoristas_veiculos_json(filial)
        return render(request, self.template_name, {
            "title": "Novo MDF-e",
            "form": form,
            "cancel_url": reverse("logistica:mdfe-list"),
            "motoristas_json": motoristas_json,
            "veiculos_json": veiculos_json,
            "nfe_documento_inicial_json": nfe_documento_inicial_json,
            "nfe_documento_id": nfe_documento.pk if nfe_documento else "",
            "rota_automatica": rota_automatica,
            "produtos_sem_peso": produtos_sem_peso,
            "endereco_origem": _endereco_filial(nfe_documento.filial)
            if nfe_documento else _endereco_filial(filial),
            "endereco_destino": (
                _endereco_destino_nfe(nfe_documento) if nfe_documento else ""
            ),
        })

    def post(self, request):
        filial = _filial(request)
        nfe_documento_id = (
            request.POST.get("nfe_documento_id")
            or request.GET.get("nfe_documento_id")
            or ""
        ).strip()
        nfe_documento = None
        if nfe_documento_id:
            nfe_documento = DocumentoFiscal.objects.filter(
                pk=nfe_documento_id, filial=filial,
                tipo_documento="nfe", status="autorizada",
            ).first()
        dados_post = request.POST.copy()
        rota_automatica = None
        produtos_sem_peso = []
        if nfe_documento:
            rota_automatica = _rota_filiais_nfe(nfe_documento)
            for campo, valor in rota_automatica.items():
                if campo not in {"origem", "destino"}:
                    dados_post[campo] = valor
            peso_carga, produtos_sem_peso = _peso_produtos_nfe(nfe_documento)
            dados_post["peso_carga_kg"] = (
                str(peso_carga) if peso_carga > 0 and not produtos_sem_peso else ""
            )
        form = MDFeForm(dados_post, filial=filial)
        if nfe_documento:
            form.fields["peso_carga_kg"].widget.attrs["readonly"] = True
        formulario_valido = form.is_valid()
        if produtos_sem_peso:
            form.add_error(
                "peso_carga_kg",
                "Cadastre o peso bruto destes produtos antes de emitir: "
                + ", ".join(produtos_sem_peso),
            )
            formulario_valido = False
        if formulario_valido:
            from apps.core.models.parametros import (
                ParametroDocumentoFiscal,
                ParametrosSistema,
            )

            with transaction.atomic():
                parametros, _ = ParametrosSistema.objects.get_or_create(filial=filial)
                ultimo_numero = (
                    MDFe.objects.for_filial(filial)
                    .select_for_update()
                    .order_by("-numero")
                    .values_list("numero", flat=True)
                    .first()
                ) or 0
                configuracao, _ = (
                    ParametroDocumentoFiscal.objects.select_for_update().get_or_create(
                        parametros=parametros,
                        tipo_documento=ParametroDocumentoFiscal.TipoDocumento.MDFE,
                        defaults={
                            "habilitado": True,
                            "serie": 1,
                            "proximo_numero": ultimo_numero + 1,
                        },
                    )
                )
                mdfe = form.save(commit=False)
                mdfe.filial = filial
                mdfe.responsavel = request.user
                mdfe.numero = max(configuracao.proximo_numero, ultimo_numero + 1)
                mdfe.serie = str(configuracao.serie or 1)
                mdfe.data_encerramento = None
                mdfe.save()
                configuracao.proximo_numero = mdfe.numero + 1
                configuracao.save(update_fields=["proximo_numero", "updated_at"])

                if nfe_documento:
                    _vincular_nfe_ao_mdfe(mdfe, nfe_documento)

            messages.success(request, f"MDF-e #{mdfe.numero:06d} criado.")
            return redirect("logistica:mdfe-detail", pk=mdfe.pk)
        motoristas_json, veiculos_json = _motoristas_veiculos_json(filial)
        return render(request, self.template_name, {
            "title": "Novo MDF-e",
            "form": form,
            "cancel_url": reverse("logistica:mdfe-list"),
            "motoristas_json": motoristas_json,
            "veiculos_json": veiculos_json,
            "nfe_documento_inicial_json": json.dumps(
                _nfe_inicial(nfe_documento), ensure_ascii=False
            ) if nfe_documento else "null",
            "nfe_documento_id": nfe_documento.pk if nfe_documento else "",
            "rota_automatica": rota_automatica,
            "produtos_sem_peso": produtos_sem_peso,
            "endereco_origem": (
                _endereco_filial(nfe_documento.filial)
                if nfe_documento else ""
            ),
            "endereco_destino": (
                _endereco_destino_nfe(nfe_documento) if nfe_documento else ""
            ),
        })


class MDFeNFeSearchView(PermissaoRequiredMixin, View):
    """Busca NF-e autorizadas da filial para vincular a um novo MDF-e."""

    permissao_modulo = "logistica"

    def get(self, request):
        filial = _filial(request)
        q_raw = request.GET.get("q", "").strip()
        q_digits = "".join(ch for ch in q_raw if ch.isdigit())
        if len(q_raw) < 2:
            return JsonResponse({"results": []})

        qs = DocumentoFiscal.objects.filter(
            filial=filial, tipo_documento="nfe", status="autorizada",
        )
        if q_digits and len(q_digits) >= 8:
            # Provavelmente uma chave de acesso (44 digitos, com ou sem espacos).
            qs = qs.filter(chave__icontains=q_digits)
        elif q_digits:
            qs = qs.filter(numero__icontains=q_digits)
        else:
            qs = qs.filter(chave__icontains=q_raw)
        results = [
            _nfe_inicial(doc)
            for doc in qs.prefetch_related("itens__produto").order_by("-data_emissao")[:15]
        ]
        return JsonResponse({"results": results})


class MDFeEmitirView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        from apps.logistica.services.mdfe_focusnfe import emitir_mdfe
        mdfe = get_object_or_404(MDFe.objects.for_filial(_filial(request)), pk=pk)
        try:
            _completar_rota_mdfe(mdfe)
            emitir_mdfe(mdfe, request.user)
            messages.success(request, "MDF-e enviado para autorização na SEFAZ.")
        except (DomainError, FocusNFeError, ValueError) as exc:
            mdfe.status = MDFe.Status.REJEITADO
            mdfe.mensagem_sefaz = str(exc)[:2000]
            mdfe.save(update_fields=["status", "mensagem_sefaz", "updated_at"])
            messages.error(request, f"Erro ao emitir MDF-e: {exc}")
        except Exception as exc:
            logger.exception(
                "Falha inesperada ao emitir MDF-e %s da filial %s",
                mdfe.pk,
                mdfe.filial_id,
            )
            detalhe = str(exc).strip() or exc.__class__.__name__
            mensagem = (
                "Não foi possível concluir a emissão do MDF-e. "
                f"Detalhe técnico: {detalhe}"
            )
            mdfe.status = MDFe.Status.REJEITADO
            mdfe.mensagem_sefaz = mensagem[:2000]
            mdfe.save(update_fields=["status", "mensagem_sefaz", "updated_at"])
            messages.error(request, mensagem)
        return redirect("logistica:mdfe-detail", pk=mdfe.pk)


class MDFeConsultarView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        from apps.logistica.services.mdfe_focusnfe import consultar_mdfe
        mdfe = get_object_or_404(MDFe.objects.for_filial(_filial(request)), pk=pk)
        try:
            mdfe = consultar_mdfe(mdfe)
            messages.success(request, f"Status atualizado: {mdfe.get_status_display()}.")
        except (DomainError, FocusNFeError, ValueError) as exc:
            messages.error(request, f"Erro ao consultar MDF-e: {exc}")
        return redirect("logistica:mdfe-detail", pk=mdfe.pk)


class MDFeCancelarFocusView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        from apps.logistica.services.mdfe_focusnfe import cancelar_mdfe
        mdfe = get_object_or_404(MDFe.objects.for_filial(_filial(request)), pk=pk)
        justificativa = request.POST.get("justificativa", "").strip()
        if len(justificativa) < 15:
            messages.error(request, "Justificativa deve ter no minimo 15 caracteres.")
            return redirect("logistica:mdfe-detail", pk=mdfe.pk)
        try:
            cancelar_mdfe(mdfe, justificativa, usuario=request.user)
            messages.success(request, "MDF-e cancelado com sucesso.")
        except (DomainError, FocusNFeError, ValueError) as exc:
            messages.error(request, f"Erro ao cancelar MDF-e: {exc}")
        return redirect("logistica:mdfe-detail", pk=mdfe.pk)


class MDFeEncerrarView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        from apps.logistica.services.mdfe_focusnfe import encerrar_mdfe
        mdfe = get_object_or_404(MDFe.objects.for_filial(_filial(request)), pk=pk)
        try:
            encerrar_mdfe(mdfe)
            messages.success(request, "MDF-e encerrado com sucesso.")
        except (DomainError, FocusNFeError, ValueError) as exc:
            messages.error(request, f"Erro ao encerrar MDF-e: {exc}")
        return redirect("logistica:mdfe-detail", pk=mdfe.pk)


class MDFeDamdfeView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"

    def get(self, request, pk):
        from apps.logistica.services.mdfe_focusnfe import damdfe_pdf
        mdfe = get_object_or_404(MDFe.objects.for_filial(_filial(request)), pk=pk)
        try:
            pdf_bytes = damdfe_pdf(mdfe)
        except (DomainError, FocusNFeError, ValueError) as exc:
            messages.error(request, f"Erro ao baixar DAMDFE: {exc}")
            return redirect("logistica:mdfe-detail", pk=mdfe.pk)
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="damdfe-{mdfe.numero:06d}.pdf"'
        return resp


class MDFeUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"
    template_name = "logistica/mdfe/form.html"
    STATUS_EDITAVEIS = {
        MDFe.Status.RASCUNHO,
        MDFe.Status.AGUARDANDO_NFE,
        MDFe.Status.REJEITADO,
    }

    def _validar_edicao(self, request, mdfe):
        if mdfe.status in self.STATUS_EDITAVEIS:
            return None
        messages.error(
            request,
            "Este MDF-e nao pode mais ser editado porque ja foi enviado para a SEFAZ.",
        )
        return redirect("logistica:mdfe-detail", pk=mdfe.pk)

    def get(self, request, pk):
        filial = _filial(request)
        mdfe = get_object_or_404(MDFe.objects.for_filial(filial), pk=pk)
        bloqueio = self._validar_edicao(request, mdfe)
        if bloqueio:
            return bloqueio
        form = MDFeForm(instance=mdfe, filial=filial)
        motoristas_json, veiculos_json = _motoristas_veiculos_json(filial)
        return render(request, self.template_name, {
            "title": f"Editar MDF-e #{mdfe.numero:06d}",
            "form": form,
            "mdfe": mdfe,
            "cancel_url": reverse("logistica:mdfe-detail", kwargs={"pk": mdfe.pk}),
            "motoristas_json": motoristas_json,
            "veiculos_json": veiculos_json,
        })

    def post(self, request, pk):
        filial = _filial(request)
        mdfe = get_object_or_404(MDFe.objects.for_filial(filial), pk=pk)
        bloqueio = self._validar_edicao(request, mdfe)
        if bloqueio:
            return bloqueio
        form = MDFeForm(request.POST, instance=mdfe, filial=filial)
        if form.is_valid():
            form.save()
            messages.success(request, f"MDF-e #{mdfe.numero:06d} atualizado.")
            return redirect("logistica:mdfe-detail", pk=mdfe.pk)
        motoristas_json, veiculos_json = _motoristas_veiculos_json(filial)
        return render(request, self.template_name, {
            "title": f"Editar MDF-e #{mdfe.numero:06d}",
            "form": form,
            "mdfe": mdfe,
            "cancel_url": reverse("logistica:mdfe-detail", kwargs={"pk": mdfe.pk}),
            "motoristas_json": motoristas_json,
            "veiculos_json": veiculos_json,
        })


class MDFeDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    template_name = "logistica/mdfe/detail.html"

    def get(self, request, pk):
        mdfe = get_object_or_404(
            MDFe.objects.for_filial(_filial(request)).select_related(
                "filial", "transportadora", "responsavel", "romaneio", "documento_fiscal"
            ),
            pk=pk,
        )
        nfe_disponiveis_qs = DocumentoFiscal.objects.filter(
            filial=mdfe.filial,
            tipo_documento="nfe",
            status="autorizada",
        ).filter(
            Q(vinculos_mdfe__isnull=True) | Q(vinculos_mdfe__mdfe=mdfe)
        ).distinct().order_by("-data_emissao", "-numero")
        if (
            mdfe.status in {
                MDFe.Status.RASCUNHO,
                MDFe.Status.AGUARDANDO_NFE,
                MDFe.Status.REJEITADO,
            }
            and not mdfe.documentos.exists()
            and nfe_disponiveis_qs.count() == 1
        ):
            try:
                with transaction.atomic():
                    _vincular_nfe_ao_mdfe(mdfe, nfe_disponiveis_qs.first())
            except ValueError:
                pass
            else:
                mdfe.refresh_from_db()

        _completar_rota_mdfe(mdfe)
        mdfe.refresh_from_db()
        documentos = mdfe.documentos.all()
        documento_form = DocumentoMDFeForm()
        nfe_disponiveis = nfe_disponiveis_qs[:30]
        editavel = mdfe.status in STATUS_MDFE_EDITAVEIS
        return render(request, self.template_name, {
            "title": f"MDF-e #{mdfe.numero:06d}",
            "mdfe": mdfe,
            "documentos": documentos,
            "tem_documentos": documentos.exists(),
            "documento_form": documento_form,
            "nfe_disponiveis": nfe_disponiveis,
            "documentos_disponiveis": (
                _documentos_disponiveis_mdfe(mdfe) if editavel else []
            ),
            "mdfe_editavel": editavel,
        })


class MDFeVincularNFeView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        filial = _filial(request)
        mdfe = get_object_or_404(MDFe.objects.for_filial(filial), pk=pk)
        if mdfe.status not in STATUS_MDFE_EDITAVEIS:
            messages.error(
                request,
                "Este MDF-e ja foi enviado e nao permite alterar a NF-e vinculada.",
            )
            return redirect("logistica:mdfe-detail", pk=mdfe.pk)

        nfe_documento = get_object_or_404(
            DocumentoFiscal,
            pk=request.POST.get("nfe_documento_id"),
            filial=filial,
            tipo_documento="nfe",
            status="autorizada",
        )
        try:
            with transaction.atomic():
                _vincular_nfe_ao_mdfe(mdfe, nfe_documento)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"NF-e n. {nfe_documento.numero}, serie {nfe_documento.serie}, "
                "vinculada ao MDF-e.",
            )
        return redirect("logistica:mdfe-detail", pk=mdfe.pk)


class MDFeVincularDocumentosView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        filial = _filial(request)
        mdfe = get_object_or_404(MDFe.objects.for_filial(filial), pk=pk)
        if mdfe.status not in STATUS_MDFE_EDITAVEIS:
            messages.error(
                request,
                "Este MDF-e ja foi enviado e nao permite alterar os documentos.",
            )
            return redirect("logistica:mdfe-detail", pk=mdfe.pk)

        selecoes = list(dict.fromkeys(request.POST.getlist("documentos")))
        if not selecoes:
            messages.error(request, "Selecione ao menos uma NF-e ou um CT-e.")
            return redirect("logistica:mdfe-detail", pk=mdfe.pk)

        vinculados = []
        try:
            with transaction.atomic():
                for selecao in selecoes:
                    try:
                        tipo, documento_id = selecao.split(":", 1)
                        documento_id = int(documento_id)
                    except (TypeError, ValueError):
                        raise ValueError("Foi selecionado um documento invalido.")

                    if tipo == "nfe":
                        documento = DocumentoFiscal.objects.filter(
                            pk=documento_id,
                            filial=filial,
                            tipo_documento="nfe",
                            status="autorizada",
                        ).first()
                        if not documento:
                            raise ValueError("Uma das NF-es selecionadas nao esta disponivel.")
                        _vincular_nfe_ao_mdfe(
                            mdfe,
                            documento,
                            atualizar_rota=not mdfe.documentos.exists(),
                        )
                        vinculados.append(f"NF-e {documento.numero}/{documento.serie}")
                    elif tipo == "cte":
                        cte = CTe.objects.for_filial(filial).filter(
                            pk=documento_id,
                            status=CTe.Status.AUTORIZADO,
                        ).first()
                        if not cte:
                            raise ValueError("Um dos CT-es selecionados nao esta disponivel.")
                        _vincular_cte_ao_mdfe(mdfe, cte)
                        vinculados.append(f"CT-e {cte.numero}/{cte.serie}")
                    else:
                        raise ValueError("Tipo de documento nao permitido.")
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"{len(vinculados)} documento(s) vinculado(s): {', '.join(vinculados)}.",
            )
        return redirect("logistica:mdfe-detail", pk=mdfe.pk)


class DocumentoMDFeCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        mdfe = get_object_or_404(MDFe.objects.for_filial(_filial(request)), pk=pk)
        if mdfe.status not in STATUS_MDFE_EDITAVEIS:
            messages.error(request, "Este MDF-e nao permite adicionar documentos.")
            return redirect("logistica:mdfe-detail", pk=mdfe.pk)
        form = DocumentoMDFeForm(request.POST)
        if form.is_valid():
            documento = form.save(commit=False)
            documento.mdfe = mdfe
            documento.save()
            mdfe.recalcular_totais()
            messages.success(request, "Documento adicionado ao MDF-e.")
        else:
            messages.error(request, "Revise os dados do documento.")
        return redirect("logistica:mdfe-detail", pk=mdfe.pk)


class DocumentoMDFeDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk, documento_pk):
        mdfe = get_object_or_404(MDFe.objects.for_filial(_filial(request)), pk=pk)
        if mdfe.status not in STATUS_MDFE_EDITAVEIS:
            messages.error(request, "Este MDF-e nao permite remover documentos.")
            return redirect("logistica:mdfe-detail", pk=mdfe.pk)
        documento = get_object_or_404(DocumentoMDFe.objects.filter(mdfe=mdfe), pk=documento_pk)
        documento.delete()
        mdfe.recalcular_totais()
        messages.success(request, "Documento removido do MDF-e.")
        return redirect("logistica:mdfe-detail", pk=mdfe.pk)


class MDFeAlterarStatusView(PermissaoRequiredMixin, View):
    permissao_modulo = "logistica"
    permissao_acao = "editar"

    def post(self, request, pk):
        mdfe = get_object_or_404(MDFe.objects.for_filial(_filial(request)), pk=pk)
        messages.error(
            request,
            "O status fiscal do MDF-e só pode ser alterado pelas ações de emitir, "
            "consultar, cancelar ou encerrar na Focus NFe.",
        )
        return redirect("logistica:mdfe-detail", pk=mdfe.pk)
