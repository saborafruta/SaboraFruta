from calendar import monthrange
from datetime import timedelta
from urllib.parse import urlencode

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
        periodo = request.GET.get("periodo", "hoje")
        data_inicio, data_fim = self._resolver_periodo(
            periodo, data_referencia, request.GET.get("data_inicio"), request.GET.get("data_fim"),
        )
        previsao_periodo = request.GET.get("previsao", "7")
        previsao_inicio, previsao_fim = self._resolver_previsao(
            previsao_periodo, data_referencia,
            request.GET.get("previsao_inicio"), request.GET.get("previsao_fim"),
        )
        mostrar_excluidos = request.GET.get("mostrar_excluidos") == "1" and _usuario_admin(request)
        mostrar_previstos = True
        posicao = PosicaoDiariaCaixaService(
            request.filial_ativa, data_fim, data_inicio=data_inicio,
        ).gerar(
            incluir_excluidos=mostrar_excluidos,
            incluir_previstos=mostrar_previstos,
            previsao_inicio=previsao_inicio,
            previsao_fim=previsao_fim,
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
            "periodo": periodo, "data_inicio": data_inicio, "data_fim": data_fim,
            "previsao_periodo": previsao_periodo,
            "previsao_inicio": previsao_inicio, "previsao_fim": previsao_fim,
            "dias_uteis_semana": self._dias_uteis_semana(data_referencia),
            "periodos_movimento": self._links_periodo(data_referencia, periodo, "periodo"),
            "periodos_previsao": self._links_periodo(data_referencia, previsao_periodo, "previsao"),
        })

    @staticmethod
    def _resolver_periodo(periodo, referencia, inicio_texto=None, fim_texto=None):
        if periodo == "7":
            return referencia - timedelta(days=6), referencia
        if periodo == "15":
            return referencia - timedelta(days=14), referencia
        if periodo == "30":
            return referencia - timedelta(days=29), referencia
        if periodo == "mes":
            return referencia.replace(day=1), referencia
        if periodo == "personalizado":
            inicio = parse_date(inicio_texto or "") or referencia
            fim = parse_date(fim_texto or "") or referencia
            return (inicio, fim) if inicio <= fim else (fim, inicio)
        return referencia, referencia

    @staticmethod
    def _resolver_previsao(periodo, referencia, inicio_texto=None, fim_texto=None):
        if periodo == "hoje":
            return referencia, referencia
        if periodo == "15":
            return referencia, referencia + timedelta(days=14)
        if periodo == "30":
            return referencia, referencia + timedelta(days=29)
        if periodo == "mes":
            ultimo = monthrange(referencia.year, referencia.month)[1]
            return referencia, referencia.replace(day=ultimo)
        if periodo == "personalizado":
            inicio = parse_date(inicio_texto or "") or referencia
            fim = parse_date(fim_texto or "") or referencia
            return (inicio, fim) if inicio <= fim else (fim, inicio)
        return referencia, referencia + timedelta(days=6)

    @staticmethod
    def _dias_uteis_semana(referencia):
        segunda = referencia - timedelta(days=referencia.weekday())
        nomes = ("Seg", "Ter", "Qua", "Qui", "Sex")
        return [
            {"data": segunda + timedelta(days=indice), "nome": nome,
             "selecionado": segunda + timedelta(days=indice) == referencia}
            for indice, nome in enumerate(nomes)
        ]

    @staticmethod
    def _links_periodo(referencia, selecionado, campo):
        opcoes = [("hoje", "Hoje"), ("7", "7 dias"), ("15", "15 dias"), ("30", "30 dias"), ("mes", "Mes atual")]
        return [
            {"valor": valor, "rotulo": rotulo, "ativo": valor == selecionado,
             "url": "?" + urlencode({"data": referencia.isoformat(), campo: valor})}
            for valor, rotulo in opcoes
        ]
