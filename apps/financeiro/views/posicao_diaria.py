from calendar import monthrange
from datetime import timedelta
from decimal import Decimal
from itertools import zip_longest
from urllib.parse import urlencode

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View

from apps.core.models import RegistroAuditoria
from apps.cadastros.models import Fornecedor, Funcionario
from apps.core.services.auditoria import registrar_auditoria, snapshot_modelo
from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.financeiro.forms import (
    EditarEntradaFinanceiraForm,
    EditarMovimentoBancarioForm,
    MovimentoContaBancariaForm,
)
from apps.financeiro.constants.enums import StatusContaPagar
from apps.financeiro.models import ContaPagar, ContaReceber, PlanoContas
from apps.financeiro.models.extrato import ExtratoBancario
from apps.financeiro.models.caixa_historico import DiaCaixaHistorico
from apps.financeiro.services.caixa_historico_service import consultar_historico
from apps.financeiro.services.posicao_diaria_service import PosicaoDiariaCaixaService
from apps.financeiro.services.receber_service import ContaReceberService
from apps.financeiro.views.contas_bancarias import ContaBancariaListView, _usuario_admin
from apps.financeiro.views.pagar import _contexto_meta_despesa_pessoal


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
                dados = form.cleaned_data.copy()
                data_referencia = dados["data_lancamento"]
                destino = reverse("financeiro:posicao_diaria") + f"?data={data_referencia.isoformat()}"
                resultado = auxiliar._salvar_movimento_manual(request, filial, dados)
                if resultado["tipo"] == MovimentoContaBancariaForm.TIPO_TRANSFERENCIA:
                    if resultado["taxa"] > 0:
                        messages.success(
                            request,
                            f"Transferencia registrada. Taxa de R$ {resultado['taxa']:.2f} "
                            f"descontada; a conta de destino recebeu R$ {resultado['liquido']:.2f}.",
                        )
                    else:
                        messages.success(request, "Transferencia registrada sem taxa.")
                else:
                    messages.success(request, "Movimento registrado na posicao diaria.")
                return redirect(destino)
            return self._render(
                request, movimento_form=form, movimento_modal=True,
                data_referencia_forcada=data_referencia,
            )
        if not _usuario_admin(request):
            messages.error(request, "Apenas administradores podem editar, excluir ou restaurar movimentos.")
            return redirect(destino)
        if acao == "editar_entrada":
            origem = request.POST.get("origem")
            registro_id = request.POST.get("movimento_id")
            if origem not in {"manual", "receber", "venda"} or not str(registro_id).isdigit():
                messages.error(request, "Entrada financeira invalida.")
                return redirect(destino)
            if origem == "manual":
                movimento_manual = get_object_or_404(
                    ExtratoBancario.objects.filter(filial=filial, origem="manual"),
                    pk=registro_id,
                )
                if movimento_manual.valor < 0:
                    messages.error(request, "Saida manual nao pode ser editada.")
                    return redirect(destino)
            form = EditarEntradaFinanceiraForm(request.POST, filial=filial, origem=origem)
            if form.is_valid():
                self._editar_entrada_financeira(
                    request, filial, origem, int(registro_id), form.cleaned_data, auxiliar,
                )
                messages.success(request, "Entrada corrigida e registrada no log financeiro.")
                return redirect(destino)
            return self._render(
                request,
                editar_entrada_form=form,
                detalhe_forcado=(origem, int(registro_id)),
                data_referencia_forcada=data_referencia,
            )
        if acao == "excluir_conta_receber":
            conta = get_object_or_404(ContaReceber.all_objects.for_filial(filial), pk=request.POST.get("conta_id"))
            motivo = (request.POST.get("motivo") or "").strip()
            try:
                antes = snapshot_modelo(conta, ["excluido_em", "excluido_por", "motivo_exclusao"])
                ContaReceberService.excluir(conta, motivo, request.user)
                conta.refresh_from_db()
                registrar_auditoria(
                    request=request, modulo=RegistroAuditoria.Modulo.FINANCEIRO,
                    acao=RegistroAuditoria.Acao.EXCLUIR, objeto=conta,
                    descricao=f"Título a receber #{conta.pk} excluído", justificativa=motivo,
                    antes=antes, depois=snapshot_modelo(conta, ["excluido_em", "excluido_por", "motivo_exclusao"]),
                )
                messages.success(request, "Conta a receber excluída. O histórico foi preservado.")
            except DomainError as exc:
                messages.error(request, str(exc))
            return redirect(destino)
        movimento = get_object_or_404(
            ExtratoBancario.objects.filter(filial=filial, origem="manual"), pk=request.POST.get("movimento_id"),
        )
        if acao == "editar_movimento":
            natureza = "saida" if movimento.valor < 0 else "entrada"
            form = EditarMovimentoBancarioForm(request.POST, filial=filial, natureza=natureza)
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

    def _render(
        self, request, movimento_form=None, movimento_modal=False, editar_movimento=None,
        editar_form=None, editar_entrada_form=None, detalhe_forcado=None,
        data_referencia_forcada=None,
    ):
        data_referencia = (
            data_referencia_forcada
            or parse_date(request.GET.get("data", ""))
            or timezone.localdate()
        )
        periodo = request.GET.get("periodo", "hoje")
        data_inicio, data_fim = self._resolver_periodo(
            periodo, data_referencia, request.GET.get("data_inicio"), request.GET.get("data_fim"),
        )
        historico = DiaCaixaHistorico.objects.for_filial(request.filial_ativa)
        fonte = request.GET.get("fonte", "")
        if fonte == "historico" or (
            request.method == "GET" and fonte != "operacional"
            and data_inicio == data_fim and historico.filter(data=data_inicio).exists()
        ):
            if periodo == "mes":
                data_fim = data_referencia.replace(day=monthrange(data_referencia.year, data_referencia.month)[1])
            contexto = consultar_historico(request.filial_ativa, data_inicio, data_fim, request.GET.get("pagina", "1"))
            parametros = request.GET.copy()
            parametros["fonte"] = "historico"
            parametros.pop("pagina", None)
            return render(request, "financeiro/posicao_diaria_historico.html", {
                "title": "Posição Diária de Caixa — Histórico importado",
                "historico": contexto, "data_referencia": data_referencia,
                "paginacao_query": parametros.urlencode(),
                "data_inicio": data_inicio, "data_fim": data_fim, "periodo": periodo,
                "dias_mes": self._dias_mes(data_referencia),
                "data_anterior": data_referencia - timedelta(days=1),
                "data_seguinte": data_referencia + timedelta(days=1),
                "url_operacional": reverse("financeiro:posicao_diaria") + "?" + urlencode({
                    "fonte": "operacional", "data": data_referencia.isoformat(), "periodo": periodo,
                    "data_inicio": data_inicio.isoformat(), "data_fim": data_fim.isoformat(),
                }),
            })
        previsao_periodo = request.GET.get("previsao", "30")
        previsao_inicio, previsao_fim = self._resolver_previsao(
            previsao_periodo, data_referencia,
            request.GET.get("previsao_inicio"), request.GET.get("previsao_fim"),
        )
        pagar_previsao_periodo = request.GET.get("pagar_previsao", "hoje")
        pagar_previsao_inicio, pagar_previsao_fim = self._resolver_previsao(
            pagar_previsao_periodo, data_referencia,
            request.GET.get("pagar_previsao_inicio"), request.GET.get("pagar_previsao_fim"),
        )
        mostrar_excluidos = request.GET.get("mostrar_excluidos") == "1" and _usuario_admin(request)
        mostrar_previstos = True
        conta_filtro_texto = request.GET.get("conta", "").strip()
        conta_filtro = int(conta_filtro_texto) if conta_filtro_texto.isdigit() else None
        ordem_movimentos = request.GET.get("ordem", "horario")
        if ordem_movimentos not in {"horario", "conta", "forma"}:
            ordem_movimentos = "horario"
        posicao = PosicaoDiariaCaixaService(
            request.filial_ativa, data_fim, data_inicio=data_inicio,
        ).gerar(
            incluir_excluidos=mostrar_excluidos,
            incluir_previstos=mostrar_previstos,
            previsao_inicio=previsao_inicio,
            previsao_fim=previsao_fim,
            conta_filtro=conta_filtro,
            ordem=ordem_movimentos,
        )
        contas_pagar_filtro = ContaPagar.objects.filter(
                filial=request.filial_ativa,
                status__in=[
                    StatusContaPagar.ABERTO,
                    StatusContaPagar.PAGO_PARCIAL,
                    StatusContaPagar.VENCIDO,
                    StatusContaPagar.AGENDADO,
                ],
                valor_saldo__gt=0,
            )
        if pagar_previsao_periodo == "personalizado":
            contas_pagar_filtro = contas_pagar_filtro.filter(
                data_vencimento__range=(pagar_previsao_inicio, pagar_previsao_fim),
            )
        else:
            # Os atalhos sempre incluem tudo que venceu antes do fim escolhido.
            # Assim, a abertura em "Hoje" não esconde títulos atrasados.
            contas_pagar_filtro = contas_pagar_filtro.filter(
                data_vencimento__lte=pagar_previsao_fim,
            )
        contas_pagar_previstas = list(
            contas_pagar_filtro
            .select_related(
                "fornecedor", "funcionario", "forma_pagamento_prevista",
                "conta_bancaria", "forma_pagamento_prevista__conta_bancaria_padrao",
            )
            .order_by("data_vencimento", "pk")
        )
        hoje = data_referencia
        for conta in contas_pagar_previstas:
            conta.previsao_conta = conta.conta_bancaria or (
                conta.forma_pagamento_prevista.conta_bancaria_padrao
                if conta.forma_pagamento_prevista_id else None
            )
            conta.previsao_conta_nome = (
                conta.previsao_conta.descricao or conta.previsao_conta.banco_nome
                if conta.previsao_conta else "Conta não definida"
            )
            conta.previsao_atrasada = conta.data_vencimento < hoje
        total_contas_pagar_previstas = sum(
            (conta.valor_saldo for conta in contas_pagar_previstas), Decimal("0.00")
        )
        detalhe = None
        origem_detalhe = detalhe_forcado[0] if detalhe_forcado else request.GET.get("origem")
        movimento_id = detalhe_forcado[1] if detalhe_forcado else request.GET.get("movimento")
        if movimento_id and str(movimento_id).isdigit():
            detalhe = next((mov for mov in [*posicao["extrato"], *posicao["excluidos"]]
                if mov.registro_id == int(movimento_id) and mov.origem_codigo == origem_detalhe), None)
        if detalhe:
            detalhe_completo = ContaBancariaListView()._detalhar_movimento(
                request.filial_ativa, detalhe.origem_codigo, detalhe.registro_id,
            )
            detalhe.historico_logs = detalhe_completo["logs"]
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
            editar_natureza = "saida" if editar_movimento.valor < 0 else "entrada"
            editar_form = EditarMovimentoBancarioForm(filial=request.filial_ativa, natureza=editar_natureza, initial={
                "conta_bancaria": editar_movimento.conta_bancaria,
                "data_lancamento": editar_movimento.data_lancamento, "valor": abs(editar_movimento.valor),
                "historico": editar_movimento.historico, "documento": editar_movimento.documento,
                "forma_pagamento": editar_movimento.forma_pagamento,
                "bandeira": editar_movimento.bandeira,
                "numero_parcelas": editar_movimento.numero_parcelas,
                "plano_contas": editar_movimento.plano_contas,
            })
        if detalhe and detalhe.entrada and _usuario_admin(request) and editar_entrada_form is None:
            item = ContaBancariaListView()._buscar_movimento_origem(
                request.filial_ativa, detalhe.origem_codigo, detalhe.registro_id,
            )
            if detalhe.origem_codigo == "manual":
                valor = item.valor
                forma = item.forma_pagamento
                conta = item.conta_bancaria
                data_entrada = item.data_lancamento
                descricao = item.historico
                plano_contas = item.plano_contas
                bandeira = item.bandeira
                numero_parcelas = item.numero_parcelas
            elif detalhe.origem_codigo == "receber":
                valor = item.valor_pago
                forma = item.forma_pagamento
                conta = item.conta_bancaria
                data_entrada = item.data_liquidacao_prevista or item.data_pagamento
                descricao = ""
                plano_contas = item.plano_contas
                bandeira = item.bandeira_recebimento
                numero_parcelas = item.parcelas_recebimento
            else:
                valor = item.valor_bruto_recebido
                forma = item.forma_pagamento
                conta = item.conta_bancaria or item.forma_pagamento.conta_bancaria_padrao
                data_entrada = item.data_liquidacao_prevista or timezone.localdate()
                descricao = ""
                plano_contas = None
                bandeira = item.bandeira
                numero_parcelas = item.numero_parcelas
            editar_entrada_form = EditarEntradaFinanceiraForm(
                filial=request.filial_ativa, origem=detalhe.origem_codigo,
                initial={
                    "valor": valor, "forma_pagamento": forma, "conta_bancaria": conta,
                    "data_entrada": data_entrada, "descricao": descricao,
                    "plano_contas": plano_contas,
                    "bandeira": bandeira, "numero_parcelas": numero_parcelas,
                },
            )
        categorias_edicao = PlanoContas.objects.none()
        grupos_edicao = PlanoContas.objects.none()
        subgrupos_edicao = PlanoContas.objects.none()
        categoria_edicao_id = ''
        subgrupo_edicao_id = ''
        grupo_edicao_id = ''
        if editar_entrada_form and 'plano_contas' in editar_entrada_form.fields:
            categorias_edicao = editar_entrada_form.fields['plano_contas'].queryset
            subgrupos_edicao = PlanoContas.objects.filter(pk__in=categorias_edicao.values_list('conta_pai_id', flat=True))
            grupos_edicao = PlanoContas.objects.filter(pk__in=subgrupos_edicao.values_list('conta_pai_id', flat=True))
            categoria_edicao_id = str(editar_entrada_form['plano_contas'].value() or '')
            categoria_selecionada = (
                categorias_edicao.filter(pk=categoria_edicao_id)
                .select_related('conta_pai__conta_pai').first()
                if categoria_edicao_id else None
            )
            if categoria_selecionada:
                subgrupo_edicao_id = str(categoria_selecionada.conta_pai_id)
                grupo_edicao_id = str(categoria_selecionada.conta_pai.conta_pai_id)
        meta_contexto = _contexto_meta_despesa_pessoal(request.filial_ativa, data_fim)
        grupo_despesa_pessoal = PlanoContas.objects.filter(
            empresa=request.filial_ativa.empresa, tipo='D', nivel=1, despesa_pessoal=True, ativo=True,
        ).order_by('codigo', 'pk').first()
        categorias_relatorio = PlanoContas.objects.filter(
            empresa=request.filial_ativa.empresa,
            ativo=True,
            aceita_lancamento=True,
        ).order_by('tipo', 'codigo', 'descricao')
        fornecedores_relatorio = Fornecedor.objects.for_filial(request.filial_ativa).filter(
            ativo=True,
        ).order_by('razao_social', 'nome_fantasia')
        funcionarios_relatorio = Funcionario.objects.for_filial(request.filial_ativa).filter(
            ativo=True,
        ).order_by('nome')
        context = {
            "historico_disponivel": historico.exists(),
            "title": "Posicao Diaria de Caixa", "data_referencia": data_referencia, "posicao": posicao,
            "hoje": timezone.localdate(),
            "movimento_form": movimento_form, "movimento_modal": movimento_modal,
            "editar_movimento": editar_movimento, "editar_form": editar_form, "detalhe": detalhe,
            "editar_entrada_form": editar_entrada_form,
            "user_is_admin": _usuario_admin(request), "mostrar_excluidos": mostrar_excluidos,
            "mostrar_previstos": mostrar_previstos,
            "periodo": periodo, "data_inicio": data_inicio, "data_fim": data_fim,
            "previsao_periodo": previsao_periodo,
            "previsao_inicio": previsao_inicio, "previsao_fim": previsao_fim,
            "dias_mes": self._dias_mes(data_referencia),
            "periodos_movimento": self._links_periodo(data_referencia, periodo, "periodo"),
            "periodos_previsao": self._links_periodo(data_referencia, previsao_periodo, "previsao"),
            "pagar_previsao_periodo": pagar_previsao_periodo,
            "pagar_previsao_inicio": pagar_previsao_inicio,
            "pagar_previsao_fim": pagar_previsao_fim,
            "periodos_pagar_previsao": self._links_periodo(
                data_referencia, pagar_previsao_periodo, "pagar_previsao",
            ),
            "contas_pagar_previstas": contas_pagar_previstas,
            "total_contas_pagar_previstas": total_contas_pagar_previstas,
            "conta_filtro": conta_filtro,
            "ordem_movimentos": ordem_movimentos,
            "categorias_edicao": categorias_edicao,
            "grupos_edicao": grupos_edicao,
            "subgrupos_edicao": subgrupos_edicao,
            "categoria_edicao_id": categoria_edicao_id,
            "subgrupo_edicao_id": subgrupo_edicao_id,
            "grupo_edicao_id": grupo_edicao_id,
            "grupo_despesa_pessoal_id": grupo_despesa_pessoal.pk if grupo_despesa_pessoal else '',
            "categorias_relatorio": categorias_relatorio,
            "fornecedores_relatorio": fornecedores_relatorio,
            "funcionarios_relatorio": funcionarios_relatorio,
            **meta_contexto,
        }
        if request.GET.get("partial") == "previsoes":
            return render(request, "financeiro/partials/previsoes_posicao_diaria.html", context)
        return render(request, self.template_name, context)

    @staticmethod
    def _editar_entrada_financeira(request, filial, origem, registro_id, dados, auxiliar):
        item = auxiliar._buscar_movimento_origem(filial, origem, registro_id)
        conta_anterior = getattr(item, "conta_bancaria", None)
        if origem == "venda":
            conta_anterior = item.conta_bancaria or item.forma_pagamento.conta_bancaria_padrao
        campos = ["conta_bancaria", "forma_pagamento"]

        if origem == "manual":
            campos += [
                "plano_contas", "data_lancamento", "data_credito", "valor", "historico",
                "bandeira", "numero_parcelas", "taxa_percentual_aplicada",
                "taxa_fixa_aplicada", "valor_taxa", "valor_liquido",
                "taxa_calculada_em", "prazo_compensacao_aplicado",
            ]
            antes = snapshot_modelo(item, campos)
            item.conta_bancaria = dados["conta_bancaria"]
            item.forma_pagamento = dados["forma_pagamento"]
            item.plano_contas = dados.get("plano_contas")
            item.data_lancamento = dados["data_entrada"]
            item.valor = dados["valor"]
            item.historico = dados.get("descricao") or item.historico
            item.bandeira = dados.get("bandeira", "")
            item.numero_parcelas = dados.get("numero_parcelas")
            item.recalcular_recebimento()
            item.save(update_fields=campos)
        elif origem == "receber":
            campos += [
                "plano_contas", "conta_contabil",
                "valor_pago", "valor_saldo", "status", "taxa_percentual_aplicada",
                "taxa_fixa_aplicada", "valor_taxa_recebimento", "valor_liquido_recebido",
                "taxa_calculada_em", "data_liquidacao_prevista",
                "bandeira_recebimento", "parcelas_recebimento",
                "prazo_compensacao_aplicado",
            ]
            antes = snapshot_modelo(item, campos)
            calculo = dados["forma_pagamento"].calcular_taxa_recebimento(
                dados["valor"], dados.get("numero_parcelas") or 1, dados.get("bandeira", ""),
            )
            item.conta_bancaria = dados["conta_bancaria"]
            item.forma_pagamento = dados["forma_pagamento"]
            item.plano_contas = dados.get("plano_contas")
            item.conta_contabil = item.plano_contas.conta_contabil if item.plano_contas else None
            item.valor_pago = dados["valor"]
            item.valor_saldo = max((item.valor_final or 0) - item.valor_pago, 0)
            item.status = "pago" if item.valor_saldo == 0 else "pago_parcial"
            item.taxa_percentual_aplicada = calculo["percentual"]
            item.taxa_fixa_aplicada = calculo["fixa"]
            item.valor_taxa_recebimento = calculo["taxa"]
            item.valor_liquido_recebido = calculo["liquido"]
            item.taxa_calculada_em = timezone.now()
            item.data_liquidacao_prevista = dados["data_entrada"]
            item.bandeira_recebimento = dados["forma_pagamento"].normalizar_bandeira(
                dados.get("bandeira", "")
            )
            item.parcelas_recebimento = dados.get("numero_parcelas")
            item.prazo_compensacao_aplicado = dados["forma_pagamento"].prazo_compensacao_dias_uteis or 0
            item.save(update_fields=[*campos, "updated_at"])
        else:
            campos += [
                "valor", "taxa_percentual_aplicada", "taxa_fixa_aplicada", "valor_taxa",
                "valor_liquido", "taxa_calculada_em", "data_liquidacao_prevista",
                "bandeira", "numero_parcelas", "prazo_compensacao_aplicado",
            ]
            antes = snapshot_modelo(item, campos)
            calculo = dados["forma_pagamento"].calcular_taxa_recebimento(
                dados["valor"], dados.get("numero_parcelas") or 1, dados.get("bandeira", ""),
            )
            item.conta_bancaria = dados["conta_bancaria"]
            item.forma_pagamento = dados["forma_pagamento"]
            item.valor = dados["valor"] + (item.troco or 0)
            item.bandeira = dados["forma_pagamento"].normalizar_bandeira(dados.get("bandeira", ""))
            item.numero_parcelas = dados.get("numero_parcelas") or 1
            item.taxa_percentual_aplicada = calculo["percentual"]
            item.taxa_fixa_aplicada = calculo["fixa"]
            item.valor_taxa = calculo["taxa"]
            item.valor_liquido = calculo["liquido"]
            item.taxa_calculada_em = timezone.now()
            item.data_liquidacao_prevista = dados["data_entrada"]
            item.prazo_compensacao_aplicado = dados["forma_pagamento"].prazo_compensacao_dias_uteis or 0
            item.save(update_fields=campos)
            venda = item.venda_pdv
            venda.valor_pago = sum((pagamento.valor_bruto_recebido for pagamento in venda.pagamentos.all()), 0)
            venda.save(update_fields=["valor_pago", "updated_at"])

        nova_conta = item.conta_bancaria
        registrar_auditoria(
            request=request,
            modulo=RegistroAuditoria.Modulo.FINANCEIRO,
            acao=RegistroAuditoria.Acao.AJUSTAR,
            objeto=item,
            relacionado=nova_conta,
            descricao=f"Entrada de {origem} corrigida na posicao diaria",
            justificativa=dados["justificativa"],
            antes=antes,
            depois=snapshot_modelo(item, campos),
            metadados={
                "contas_envolvidas": list(filter(None, [
                    getattr(conta_anterior, "pk", None), nova_conta.pk,
                ])),
                "origem_movimento": origem,
            },
        )
        if conta_anterior:
            auxiliar._atualizar_saldo_conta(conta_anterior)
        if not conta_anterior or conta_anterior.pk != nova_conta.pk:
            auxiliar._atualizar_saldo_conta(nova_conta)

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
    def _dias_mes(referencia):
        nomes = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom")
        hoje = timezone.localdate()
        ultimo = monthrange(referencia.year, referencia.month)[1]
        return [
            {
                "data": referencia.replace(day=dia),
                "nome": nomes[referencia.replace(day=dia).weekday()],
                "selecionado": referencia.day == dia,
                "hoje": referencia.replace(day=dia) == hoje,
            }
            for dia in range(1, ultimo + 1)
        ]

    @staticmethod
    def _links_periodo(referencia, selecionado, campo):
        opcoes = [("hoje", "Hoje"), ("7", "7 dias"), ("15", "15 dias"), ("30", "30 dias"), ("mes", "Mes atual")]
        return [
            {"valor": valor, "rotulo": rotulo, "ativo": valor == selecionado,
             "url": "?" + urlencode({"data": referencia.isoformat(), campo: valor})}
            for valor, rotulo in opcoes
        ]


class PosicaoDiariaCaixaRelatorioView(PermissaoRequiredMixin, View):
    """Versao compacta e imprimivel da posicao operacional selecionada."""

    permissao_modulo = "financeiro"
    permissao_acao = "ver"
    template_name = "financeiro/posicao_diaria_relatorio.html"

    def get(self, request):
        data_referencia = parse_date(request.GET.get("data", "")) or timezone.localdate()
        periodo = request.GET.get("periodo", "hoje")
        data_inicio, data_fim = PosicaoDiariaCaixaView._resolver_periodo(
            periodo,
            data_referencia,
            request.GET.get("data_inicio"),
            request.GET.get("data_fim"),
        )
        conta_texto = request.GET.get("conta", "").strip()
        conta_filtro = int(conta_texto) if conta_texto.isdigit() else None
        def filtro_id(nome):
            valor = request.GET.get(nome, "").strip()
            return int(valor) if valor.isdigit() else None

        categoria_filtro = filtro_id("categoria")
        fornecedor_filtro = filtro_id("fornecedor")
        funcionario_filtro = filtro_id("funcionario")
        ordem = "horario"
        posicao = PosicaoDiariaCaixaService(
            request.filial_ativa, data_fim, data_inicio=data_inicio,
        ).gerar(
            conta_filtro=conta_filtro,
            ordem=ordem,
            categoria_filtro=categoria_filtro,
            fornecedor_filtro=fornecedor_filtro,
            funcionario_filtro=funcionario_filtro,
        )
        categorias = PlanoContas.objects.filter(
            empresa=request.filial_ativa.empresa,
            ativo=True,
            aceita_lancamento=True,
        ).order_by("tipo", "codigo", "descricao")
        fornecedores = Fornecedor.objects.for_filial(request.filial_ativa).filter(
            ativo=True,
        ).order_by("razao_social", "nome_fantasia")
        funcionarios = Funcionario.objects.for_filial(request.filial_ativa).filter(
            ativo=True,
        ).order_by("nome")
        categoria_selecionada = categorias.filter(pk=categoria_filtro).first()
        fornecedor_selecionado = fornecedores.filter(pk=fornecedor_filtro).first()
        funcionario_selecionado = funcionarios.filter(pk=funcionario_filtro).first()
        nomes_semana = (
            "Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira",
            "Sexta-feira", "Sábado", "Domingo",
        )
        datas_movimentos = sorted(
            {mov.data for mov in posicao["extrato"]}, reverse=True,
        )
        dias_relatorio = []
        for data_movimento in datas_movimentos:
            entradas = [mov for mov in posicao["entradas"] if mov.data == data_movimento]
            saidas = [mov for mov in posicao["saidas"] if mov.data == data_movimento]
            dias_relatorio.append({
                "data": data_movimento,
                "dia_semana": nomes_semana[data_movimento.weekday()],
                "linhas": [
                    {"entrada": entrada, "saida": saida}
                    for entrada, saida in zip_longest(entradas, saidas)
                ],
            })
        blocos_relatorio = []
        linhas_na_pagina = 0
        capacidade_pagina = 18
        maximo_linhas_bloco = 16
        for dia in dias_relatorio:
            linhas = dia["linhas"]
            for indice in range(0, len(linhas), maximo_linhas_bloco):
                linhas_bloco = linhas[indice:indice + maximo_linhas_bloco]
                quebra_antes = bool(
                    linhas_na_pagina
                    and linhas_na_pagina + len(linhas_bloco) > capacidade_pagina
                )
                if quebra_antes:
                    linhas_na_pagina = 0
                blocos_relatorio.append({
                    "data": dia["data"],
                    "dia_semana": dia["dia_semana"],
                    "linhas": linhas_bloco,
                    "continuacao": indice > 0,
                    "quebra_antes": quebra_antes,
                })
                linhas_na_pagina += len(linhas_bloco)
        filtros_selecionados = [
            item for item in (
                f"Categoria: {categoria_selecionada.caminho_descricao}" if categoria_selecionada else "",
                f"Fornecedor: {fornecedor_selecionado}" if fornecedor_selecionado else "",
                f"Funcionário: {funcionario_selecionado}" if funcionario_selecionado else "",
            ) if item
        ]
        retorno = reverse("financeiro:posicao_diaria") + "?" + urlencode({
            "data": data_referencia.isoformat(),
            "periodo": periodo,
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
            **({"conta": conta_filtro} if conta_filtro else {}),
            **({"categoria": categoria_filtro} if categoria_filtro else {}),
            **({"fornecedor": fornecedor_filtro} if fornecedor_filtro else {}),
            **({"funcionario": funcionario_filtro} if funcionario_filtro else {}),
            "ordem": ordem,
        })
        return render(request, self.template_name, {
            "title": "Relatorio da Posicao de Caixa",
            "filial": request.filial_ativa,
            "posicao": posicao,
            "data_referencia": data_referencia,
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "periodo_unico": data_inicio == data_fim,
            "dias_relatorio": dias_relatorio,
            "blocos_relatorio": blocos_relatorio,
            "filtros_selecionados": filtros_selecionados,
            "filtros_ativos": bool(filtros_selecionados),
            "gerado_em": timezone.localtime(),
            "retorno_url": retorno,
        })
