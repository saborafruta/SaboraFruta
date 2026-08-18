from django.db.models import Count, Q
from django.shortcuts import render
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.financeiro.models import PlanoContabil


class PlanoContabilListView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "ver"
    template_name = "financeiro/plano_contabil/list.html"

    def get(self, request):
        empresa = request.filial_ativa.empresa if request.filial_ativa else None
        base_qs = PlanoContabil.objects.none()
        if empresa:
            base_qs = PlanoContabil.objects.filter(empresa=empresa)

        q = request.GET.get("q", "").strip()
        grupo = request.GET.get("grupo", "").strip()
        tipo = request.GET.get("tipo", "").strip()
        status = request.GET.get("status", "").strip()

        contas = base_qs.select_related("conta_pai").order_by("ordem")
        if grupo in {"1", "2", "3", "4", "5"}:
            contas = contas.filter(classificacao__startswith=grupo)
        else:
            grupo = ""
        if tipo in {PlanoContabil.TipoConta.SINTETICA, PlanoContabil.TipoConta.ANALITICA}:
            contas = contas.filter(tipo_conta=tipo)
        else:
            tipo = ""
        if status == "ativo":
            contas = contas.filter(ativo=True)
        elif status == "inativo":
            contas = contas.filter(ativo=False)
        else:
            status = ""
        if q:
            contas = contas.filter(
                Q(classificacao__icontains=q)
                | Q(descricao__icontains=q)
                | Q(codigo_referencia__icontains=q)
            )

        totais = base_qs.aggregate(
            total=Count("id"),
            sinteticas=Count("id", filter=Q(tipo_conta=PlanoContabil.TipoConta.SINTETICA)),
            analiticas=Count("id", filter=Q(tipo_conta=PlanoContabil.TipoConta.ANALITICA)),
            inativas=Count("id", filter=Q(ativo=False)),
        )
        grupos = list(base_qs.filter(nivel=1).order_by("ordem"))

        return render(request, self.template_name, {
            "contas": contas,
            "total_filtrado": contas.count(),
            "totais": totais,
            "grupos": grupos,
            "q": q,
            "grupo": grupo,
            "tipo": tipo,
            "status": status,
        })
