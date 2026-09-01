"""Audita e, sob confirmação explícita, repara cancelamentos antigos do PDV."""
from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import RegistroAuditoria
from apps.core.services.auditoria import registrar_auditoria
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import StatusContaReceber
from apps.financeiro.models import ContaReceber
from apps.pdv.models import SessaoPDV, VendaPDV


STATUS_ENCERRADOS = (StatusContaReceber.CANCELADO, StatusContaReceber.DEVOLVIDO)
CONFIRMACAO = "CORRIGIR_CANCELAMENTOS_PDV"


def _dinheiro(valor):
    return str(Decimal(valor or 0).quantize(Decimal("0.01")))


def _chave_movimento(movimento):
    return movimento.produto_id, movimento.lote_id


def _movimentos_esperados(venda):
    ids = {
        movimento_id
        for item in venda.itens.all()
        if item.estoque_baixado
        for movimento_id in (item.movimentacoes_estoque_ids or [])
    }
    movimentos = list(
        MovimentacaoEstoque.objects.filter(pk__in=ids, filial=venda.filial)
        .select_related("produto", "lote")
    )
    return ids, movimentos


def _faltas_estoque(venda):
    ids, originais = _movimentos_esperados(venda)
    esperado = defaultdict(Decimal)
    origem_por_chave = {}
    for movimento in originais:
        chave = _chave_movimento(movimento)
        esperado[chave] += movimento.quantidade
        origem_por_chave[chave] = movimento

    devolvido = defaultdict(Decimal)
    devolucoes = MovimentacaoEstoque.objects.filter(
        filial=venda.filial,
        documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
        documento_id=venda.pk,
        tipo_operacao=MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
        observacao__startswith=f"Estorno da venda #{venda.numero_venda}:",
    )
    for movimento in devolucoes:
        devolvido[_chave_movimento(movimento)] += movimento.quantidade

    faltas = []
    excessos = []
    for chave in set(esperado) | set(devolvido):
        diferenca = esperado[chave] - devolvido[chave]
        origem = origem_por_chave.get(chave)
        item = {
            "produto_id": chave[0],
            "produto": origem.produto.descricao if origem else "",
            "lote_id": chave[1],
            "esperado": str(esperado[chave]),
            "devolvido": str(devolvido[chave]),
            "diferenca": str(abs(diferenca)),
        }
        if diferenca > 0:
            item["origem"] = origem
            faltas.append(item)
        elif diferenca < 0:
            excessos.append(item)
    return {
        "ids_referenciados": sorted(ids),
        "ids_encontrados": sorted(m.pk for m in originais),
        "faltas": faltas,
        "excessos": excessos,
    }


def _valor_venda_para_caixa(venda):
    nao_contabilizado = sum(
        (pagamento.valor - pagamento.troco)
        for pagamento in venda.pagamentos.select_related("forma_pagamento")
        if not pagamento.forma_pagamento.movimenta_caixa
    )
    return max(Decimal("0"), (venda.valor_total or Decimal("0")) - nao_contabilizado)


def _recalcular_sessao(sessao_id):
    sessao = SessaoPDV.objects.select_for_update().get(pk=sessao_id)
    anterior = sessao.total_vendas
    esperado = sum(
        (_valor_venda_para_caixa(venda) for venda in sessao.vendas.filter(status="finalizada")),
        Decimal("0"),
    )
    if anterior != esperado:
        sessao.total_vendas = esperado
        sessao.save(update_fields=["total_vendas"])
    return anterior, esperado


def _auditar_sessao(sessao):
    esperado = sum(
        (_valor_venda_para_caixa(venda) for venda in sessao.vendas.filter(status="finalizada")),
        Decimal("0"),
    )
    return {
        "sessao_id": sessao.pk,
        "filial_id": sessao.filial_id,
        "status": sessao.status,
        "registrado": _dinheiro(sessao.total_vendas),
        "esperado": _dinheiro(esperado),
        "diferenca": _dinheiro((sessao.total_vendas or Decimal("0")) - esperado),
        "corrigida": False,
        "correcao_bloqueada": sessao.status != "aberto",
    }


class Command(BaseCommand):
    help = (
        "Audita vendas canceladas contra Contas a Receber, estoque e sessões. "
        "É somente leitura por padrão."
    )

    def add_arguments(self, parser):
        parser.add_argument("--filial-id", type=int)
        parser.add_argument("--venda", action="append", type=int, dest="vendas")
        parser.add_argument("--json", action="store_true", dest="como_json")
        parser.add_argument("--corrigir", action="store_true")
        parser.add_argument("--confirmar", default="")

    def handle(self, *args, **options):
        corrigir = options["corrigir"]
        if corrigir and options["confirmar"] != CONFIRMACAO:
            raise CommandError(
                f"Para corrigir, informe --confirmar {CONFIRMACAO}. "
                "Sem --corrigir o comando é somente leitura."
            )

        vendas = (
            VendaPDV.objects.filter(status="cancelada")
            .select_related("filial", "usuario", "cancelado_por", "sessao_pdv")
            .prefetch_related("itens", "pagamentos__forma_pagamento")
            .order_by("filial_id", "numero_venda")
        )
        if options["filial_id"]:
            vendas = vendas.filter(filial_id=options["filial_id"])
        if options["vendas"]:
            vendas = vendas.filter(numero_venda__in=options["vendas"])

        relatorio = {
            "modo": "correcao" if corrigir else "auditoria",
            "vendas_canceladas_analisadas": 0,
            "vendas_com_divergencia": [],
            "vendas_bloqueadas_por_recebimento": [],
            "vendas_ativas_com_titulo_cancelado": [],
            "titulos_orfaos": [],
            "sessoes_divergentes": [],
            "sessoes_recalculadas": [],
        }
        sessoes = set()

        with transaction.atomic():
            for venda in vendas:
                relatorio["vendas_canceladas_analisadas"] += 1
                contas_qs = ContaReceber.objects.filter(
                        filial=venda.filial,
                        documento_tipo="venda_pdv",
                        documento_id=venda.pk,
                    ).prefetch_related("pagamentos")
                if corrigir:
                    contas_qs = contas_qs.select_for_update()
                contas = list(contas_qs)
                contas_ativas = [c for c in contas if c.status not in STATUS_ENCERRADOS]
                contas_recebidas = [
                    c for c in contas
                    if c.valor_pago > 0 or c.pagamentos.exists()
                    or c.status in (StatusContaReceber.PAGO, StatusContaReceber.PAGO_PARCIAL)
                ]
                estoque = _faltas_estoque(venda)
                referencias_ausentes = sorted(
                    set(estoque["ids_referenciados"]) - set(estoque["ids_encontrados"])
                )
                tem_divergencia = bool(
                    contas_ativas or estoque["faltas"] or estoque["excessos"]
                    or referencias_ausentes or venda.cancelado_em is None
                )
                if not tem_divergencia:
                    continue

                item = {
                    "venda_id": venda.pk,
                    "numero_venda": venda.numero_venda,
                    "filial_id": venda.filial_id,
                    "filial": str(venda.filial),
                    "cancelado_em": str(venda.cancelado_em or ""),
                    "motivo": venda.motivo_cancelamento,
                    "contas_ativas": [
                        {
                            "id": conta.pk,
                            "status": conta.status,
                            "valor_pago": _dinheiro(conta.valor_pago),
                            "valor_saldo": _dinheiro(conta.valor_saldo),
                        }
                        for conta in contas_ativas
                    ],
                    "estoque_faltante": [
                        {k: v for k, v in falta.items() if k != "origem"}
                        for falta in estoque["faltas"]
                    ],
                    "estoque_excedente": estoque["excessos"],
                    "referencias_movimento_ausentes": referencias_ausentes,
                    "corrigida": False,
                }
                relatorio["vendas_com_divergencia"].append(item)

                if contas_recebidas:
                    relatorio["vendas_bloqueadas_por_recebimento"].append({
                        "numero_venda": venda.numero_venda,
                        "contas": [c.pk for c in contas_recebidas],
                    })
                    continue
                if not corrigir or estoque["excessos"] or referencias_ausentes:
                    continue

                usuario = venda.cancelado_por or venda.usuario
                motivo = (
                    f"Reparo de integridade da venda cancelada #{venda.numero_venda}. "
                    f"Motivo original: {venda.motivo_cancelamento or 'não informado'}"
                )
                contas_corrigidas = []
                for conta in contas_ativas:
                    conta.status = StatusContaReceber.CANCELADO
                    nota = f"[Reparo automático de integridade] {motivo}"
                    conta.observacao = f"{conta.observacao}\n{nota}".strip()
                    conta.save(update_fields=["status", "observacao", "updated_at"])
                    contas_corrigidas.append(conta.pk)

                movimentos_criados = []
                for falta in estoque["faltas"]:
                    origem = falta["origem"]
                    movimento = MovimentacaoService.registrar_movimentacao(
                        produto_id=origem.produto_id,
                        filial_id=origem.filial_id,
                        tipo_operacao=MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
                        quantidade=Decimal(falta["diferenca"]),
                        usuario_id=usuario.pk,
                        lote_id=origem.lote_id,
                        documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
                        documento_id=venda.pk,
                        documento_numero=str(venda.numero_venda),
                        observacao=f"Estorno da venda #{venda.numero_venda}: {motivo}",
                        permitir_sem_lote=True,
                    )
                    movimentos_criados.append(movimento.pk)

                if venda.sessao_pdv_id:
                    sessoes.add(venda.sessao_pdv_id)
                registrar_auditoria(
                    usuario=usuario,
                    filial=venda.filial,
                    modulo=RegistroAuditoria.Modulo.FINANCEIRO,
                    acao=RegistroAuditoria.Acao.AJUSTAR,
                    objeto=venda,
                    descricao=f"Reparo do cancelamento da venda #{venda.numero_venda}",
                    justificativa=motivo,
                    metadados={
                        "contas_canceladas": contas_corrigidas,
                        "movimentacoes_estoque_criadas": movimentos_criados,
                        "comando": "auditar_cancelamentos_pdv",
                    },
                )
                item["corrigida"] = True

            sessoes_canceladas = SessaoPDV.objects.filter(
                vendas__status="cancelada",
            ).distinct().prefetch_related("vendas__pagamentos__forma_pagamento")
            if options["filial_id"]:
                sessoes_canceladas = sessoes_canceladas.filter(filial_id=options["filial_id"])
            if options["vendas"]:
                sessoes_canceladas = sessoes_canceladas.filter(
                    vendas__status="cancelada",
                    vendas__numero_venda__in=options["vendas"],
                )
            for sessao in sessoes_canceladas.order_by("pk"):
                item_sessao = _auditar_sessao(sessao)
                if item_sessao["registrado"] == item_sessao["esperado"]:
                    continue
                relatorio["sessoes_divergentes"].append(item_sessao)
                if corrigir and sessao.pk in sessoes and sessao.status == "aberto":
                    anterior, esperado = _recalcular_sessao(sessao.pk)
                    item_sessao["corrigida"] = True
                    relatorio["sessoes_recalculadas"].append({
                        "sessao_id": sessao.pk,
                        "anterior": _dinheiro(anterior),
                        "esperado": _dinheiro(esperado),
                    })

        base_contas = ContaReceber.objects.filter(documento_tipo="venda_pdv")
        if options["filial_id"]:
            base_contas = base_contas.filter(filial_id=options["filial_id"])
        vendas_existentes = VendaPDV.objects.values("pk")
        relatorio["titulos_orfaos"] = list(
            base_contas.exclude(documento_id__in=vendas_existentes)
            .values("id", "filial_id", "documento_id", "documento_numero", "status", "valor_saldo")
        )
        relatorio["vendas_ativas_com_titulo_cancelado"] = list(
            base_contas.filter(
                documento_id__in=VendaPDV.objects.filter(status="finalizada").values("pk"),
                status=StatusContaReceber.CANCELADO,
            ).values("id", "filial_id", "documento_id", "documento_numero", "valor_saldo")
        )

        serializado = json.dumps(relatorio, ensure_ascii=False, default=str, indent=2)
        if options["como_json"]:
            self.stdout.write(serializado)
        else:
            self.stdout.write(
                f"Modo: {relatorio['modo']}\n"
                f"Vendas canceladas analisadas: {relatorio['vendas_canceladas_analisadas']}\n"
                f"Vendas com divergência: {len(relatorio['vendas_com_divergencia'])}\n"
                f"Bloqueadas por recebimento: {len(relatorio['vendas_bloqueadas_por_recebimento'])}\n"
                f"Sessões divergentes: {len(relatorio['sessoes_divergentes'])}\n"
                f"Títulos órfãos: {len(relatorio['titulos_orfaos'])}\n"
                f"Vendas ativas com título cancelado: "
                f"{len(relatorio['vendas_ativas_com_titulo_cancelado'])}\n"
            )
