from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View

from apps.core.models import RegistroAuditoria
from apps.core.services.auditoria import registrar_auditoria, snapshot_modelo
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.financeiro.forms import EditarMovimentoBancarioForm, MovimentoContaBancariaForm
from apps.financeiro.models.extrato import ExtratoBancario
from apps.financeiro.services.posicao_diaria_service import PosicaoDiariaCaixaService
from apps.financeiro.views.contas_bancarias import ContaBancariaListView, _usuario_admin


class PosicaoDiariaCaixaView(PermissaoRequiredMixin, View):
    permissao_modulo = "financeiro"
    permissao_acao = "ver"
    template_name = "financeiro/posicao_diaria.html"

    def get(self, request):
        return self._render(request)

    @transaction.atomic
    def post(self, request):
        acao = request.POST.get("acao")
        filial = request.filial_ativa
        data_referencia = parse_date(request.POST.get("data_referencia", "")) or timezone.localdate()
        destino = reverse("financeiro:posicao_diaria") + f"?data={data_referencia.isoformat()}"
        auxiliar = ContaBancariaListView()
        if acao == "lancar_movimento":
            form = MovimentoContaBancariaForm(request.POST, filial=filial)
            if form.is_valid():
                auxiliar._salvar_movimento_manual(request, filial, form.cleaned_data)
                messages.success(request, "Movimento registrado na posicao diaria.")
                return redirect(destino)
            return self._render(request, movimento_form=form, movimento_modal=True)
        if not _usuario_admin(request):
            messages.error(request, "Apenas administradores podem editar, excluir ou restaurar movimentos.")
            return redirect(destino)
        movimento = get_object_or_404(
            ExtratoBancario.objects.filter(filial=filial, origem="manual"), pk=request.POST.get("movimento_id"),
        )
        if acao == "editar_movimento":
            form = EditarMovimentoBancarioForm(request.POST, filial=filial)
            if form.is_valid():
                auxiliar._editar_movimento_manual(request, movimento, form.cleaned_data)
                messages.success(request, "Movimento corrigido e registrado no log.")
                return redirect(destino)
            return self._render(request, editar_movimento=movimento, editar_form=form)
        if acao in {"excluir_movimento", "restaurar_movimento"}:
            motivo = (request.POST.get("justificativa") or "").strip()
            if not motivo:
                messages.error(request, "Informe o motivo da alteracao.")
                return redirect(destino)
            antes = snapshot_modelo(movimento, ["status"])
            movimento.status = "excluido" if acao == "excluir_movimento" else "importado"
            movimento.save(update_fields=["status"])
            registrar_auditoria(
                request=request, modulo=RegistroAuditoria.Modulo.FINANCEIRO,
                acao=RegistroAuditoria.Acao.EXCLUIR if acao == "excluir_movimento" else RegistroAuditoria.Acao.RESTAURAR,
                objeto=movimento, relacionado=movimento.conta_bancaria,
                descricao="Movimento manual excluido" if acao == "excluir_movimento" else "Movimento manual restaurado",
                justificativa=motivo, antes=antes, depois=snapshot_modelo(movimento, ["status"]),
                metadados={"contas_envolvidas": [movimento.conta_bancaria_id]},
            )
            auxiliar._atualizar_saldo_conta(movimento.conta_bancaria)
            messages.success(request, "Movimento excluido." if acao == "excluir_movimento" else "Movimento restaurado.")
            return redirect(destino)
        messages.error(request, "Acao invalida.")
        return redirect(destino)

    def _render(self, request, movimento_form=None, movimento_modal=False, editar_movimento=None, editar_form=None):
        data_referencia = parse_date(request.GET.get("data", "")) or timezone.localdate()
        mostrar_excluidos = request.GET.get("mostrar_excluidos") == "1" and _usuario_admin(request)
        mostrar_previstos = request.GET.get("mostrar_previstos") == "1"
        posicao = PosicaoDiariaCaixaService(request.filial_ativa, data_referencia).gerar(
            incluir_excluidos=mostrar_excluidos,
            incluir_previstos=mostrar_previstos,
        )
        detalhe = None
        movimento_id = request.GET.get("movimento")
        if movimento_id and str(movimento_id).isdigit():
            detalhe = next((mov for mov in [*posicao["extrato"], *posicao["excluidos"]]
                if mov.registro_id == int(movimento_id) and mov.origem_codigo == request.GET.get("origem")), None)
        if movimento_form is None:
            movimento_form = MovimentoContaBancariaForm(
                filial=request.filial_ativa, initial={"data_lancamento": data_referencia},
            )
        if editar_movimento is None and request.GET.get("editar") and _usuario_admin(request):
            editar_movimento = get_object_or_404(
                ExtratoBancario.objects.filter(filial=request.filial_ativa, origem="manual"),
                pk=request.GET.get("editar"),
            )
        if editar_form is None and editar_movimento:
            editar_form = EditarMovimentoBancarioForm(filial=request.filial_ativa, initial={
                "conta_bancaria": editar_movimento.conta_bancaria,
                "data_lancamento": editar_movimento.data_lancamento, "valor": editar_movimento.valor,
                "historico": editar_movimento.historico, "documento": editar_movimento.documento,
            })
        return render(request, self.template_name, {
            "title": "Posicao Diaria de Caixa", "data_referencia": data_referencia, "posicao": posicao,
            "movimento_form": movimento_form, "movimento_modal": movimento_modal,
            "editar_movimento": editar_movimento, "editar_form": editar_form, "detalhe": detalhe,
            "user_is_admin": _usuario_admin(request), "mostrar_excluidos": mostrar_excluidos,
            "mostrar_previstos": mostrar_previstos,
        })
