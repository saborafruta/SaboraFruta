from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.financeiro.models import ContaBancaria
from apps.financeiro.models.extrato import ExtratoBancario
from apps.financeiro.models.receber_pagar import ContaReceber, PagamentoContaPagar
from apps.core.services.calendario import adicionar_dias_uteis_bancarios


ZERO = Decimal("0")


@dataclass
class MovimentoDiario:
    data: date
    conta: ContaBancaria
    descricao: str
    contraparte: str
    origem: str
    origem_codigo: str
    registro_id: int
    documento: str = ""
    forma_pagamento: str = "Nao informada"
    entrada: Decimal = ZERO
    saida: Decimal = ZERO
    valor_bruto: Decimal = ZERO
    valor_taxa: Decimal = ZERO
    referencia_url: str = ""
    excluido: bool = False

    @property
    def valor(self):
        return self.entrada - self.saida


def _somar(destino, conta_id, valor):
    if conta_id:
        destino[conta_id] += valor or ZERO


class PosicaoDiariaCaixaService:
    """Consolida o livro-caixa operacional sem misturar valores projetados."""

    CORES = ("azul", "verde", "ambar", "violeta", "cinza")

    def __init__(self, filial, data_referencia):
        self.filial = filial
        self.data = data_referencia
        self.contas = list(
            ContaBancaria.objects.for_filial(filial)
            .filter(ativo=True)
            .order_by("descricao", "banco_nome", "pk")
        )
        self.conta_ids = {conta.pk for conta in self.contas}

    def gerar(self, *, incluir_excluidos=False, incluir_previstos=False):
        movimentos = self._movimentos_do_dia(incluir_excluidos=incluir_excluidos)
        movimentos_ativos = [mov for mov in movimentos if not mov.excluido]
        entradas = [mov for mov in movimentos_ativos if mov.entrada > ZERO]
        saidas = [mov for mov in movimentos_ativos if mov.saida > ZERO]
        saldo_anterior = self._saldos_antes_do_dia()
        por_conta_dia = defaultdict(lambda: ZERO)
        for mov in movimentos_ativos:
            por_conta_dia[mov.conta.pk] += mov.valor

        contas = []
        for indice, conta in enumerate(self.contas):
            abertura = (conta.saldo_inicial or ZERO) + saldo_anterior[conta.pk]
            fechamento = abertura + por_conta_dia[conta.pk]
            nome_base = " ".join(filter(None, [conta.descricao, conta.banco_nome, conta.tipo_conta])).casefold()
            eh_dinheiro = "dinheiro" in nome_base or "caixa" in nome_base
            conta.posicao_abertura = abertura
            conta.posicao_entradas = sum((m.entrada for m in entradas if m.conta.pk == conta.pk), ZERO)
            conta.posicao_saidas = sum((m.saida for m in saidas if m.conta.pk == conta.pk), ZERO)
            conta.posicao_fechamento = fechamento
            conta.posicao_cor = "azul" if eh_dinheiro else self.CORES[indice % len(self.CORES)]
            conta.eh_dinheiro = eh_dinheiro
            contas.append(conta)

        total_abertura = sum((c.posicao_abertura for c in contas), ZERO)
        total_entradas = sum((m.entrada for m in entradas), ZERO)
        total_saidas = sum((m.saida for m in saidas), ZERO)
        total_fechamento = sum((c.posicao_fechamento for c in contas), ZERO)
        previsoes = self._recebimentos_previstos() if incluir_previstos else []
        return {
            "contas": contas,
            "entradas": entradas,
            "saidas": saidas,
            "extrato": sorted(movimentos_ativos, key=lambda m: (m.data, m.registro_id, m.origem_codigo)),
            "excluidos": [mov for mov in movimentos if mov.excluido],
            "total_abertura": total_abertura,
            "total_entradas": total_entradas,
            "total_saidas": total_saidas,
            "total_fechamento": total_fechamento,
            "variacao_dia": total_entradas - total_saidas,
            "totais_forma_entrada": self._agrupar(entradas, "forma_pagamento", "entrada"),
            "totais_forma_saida": self._agrupar(saidas, "forma_pagamento", "saida"),
            "totais_conta_entrada": self._agrupar(entradas, "conta", "entrada"),
            "totais_conta_saida": self._agrupar(saidas, "conta", "saida"),
            "sem_conta": self._pendencias_sem_conta_do_dia(),
            "possui_caixa_dinheiro": any(conta.eh_dinheiro for conta in contas),
            "previsoes": previsoes,
            "total_previsto": sum((item["valor_liquido"] for item in previsoes), ZERO),
        }

    @staticmethod
    def _agrupar(movimentos, atributo, campo_valor):
        totais = defaultdict(lambda: ZERO)
        for movimento in movimentos:
            chave = getattr(movimento, atributo)
            if atributo == "conta":
                chave = chave.descricao or chave.banco_nome or f"Conta #{chave.pk}"
            totais[chave or "Nao informada"] += getattr(movimento, campo_valor)
        return [{"nome": nome, "valor": valor} for nome, valor in sorted(
            totais.items(), key=lambda item: (-item[1], item[0].casefold())
        )]

    def _movimentos_do_dia(self, *, incluir_excluidos=False):
        movimentos = []
        manuais = ExtratoBancario.objects.filter(
            filial=self.filial, conta_bancaria_id__in=self.conta_ids, data_lancamento=self.data,
        ).select_related("conta_bancaria")
        if not incluir_excluidos:
            manuais = manuais.exclude(status="excluido")
        for item in manuais:
            valor = item.valor or ZERO
            movimentos.append(MovimentoDiario(
                data=item.data_lancamento, conta=item.conta_bancaria,
                descricao=item.historico or "Lancamento manual", contraparte="Movimento manual",
                origem="Manual" if item.origem == "manual" else "Extrato bancario",
                origem_codigo="manual", registro_id=item.pk, documento=item.documento,
                entrada=max(valor, ZERO), saida=abs(min(valor, ZERO)), excluido=item.status == "excluido",
            ))

        recebimentos = ContaReceber.objects.filter(
            filial=self.filial, conta_bancaria_id__in=self.conta_ids,
            valor_pago__gt=0,
        ).filter(
            Q(data_liquidacao_prevista=self.data)
            | Q(data_liquidacao_prevista__isnull=True, data_pagamento=self.data)
        ).select_related("conta_bancaria", "cliente", "forma_pagamento")
        for item in recebimentos:
            bruto = item.valor_pago or ZERO
            taxa = item.valor_taxa_recebimento if item.taxa_calculada_em else ZERO
            movimentos.append(MovimentoDiario(
                data=item.data_liquidacao_prevista or item.data_pagamento, conta=item.conta_bancaria,
                descricao=f"Recebimento de {item.cliente}", contraparte=str(item.cliente),
                origem="Conta a receber", origem_codigo="receber", registro_id=item.pk,
                documento=item.documento_numero,
                forma_pagamento=item.forma_pagamento.descricao if item.forma_pagamento else "Nao informada",
                entrada=item.valor_entrada_liquida, valor_bruto=bruto, valor_taxa=taxa,
                referencia_url=reverse("financeiro:receber_detail", args=[item.pk]),
            ))

        pagamentos = PagamentoContaPagar.objects.filter(
            filial=self.filial, conta_bancaria_id__in=self.conta_ids,
            data_pagamento=self.data, conta_pagar__excluido_em__isnull=True,
        ).select_related(
            "conta_bancaria", "forma_pagamento", "conta_pagar__fornecedor", "conta_pagar__funcionario",
        )
        for item in pagamentos:
            movimentos.append(MovimentoDiario(
                data=item.data_pagamento, conta=item.conta_bancaria,
                descricao=f"Pagamento para {item.conta_pagar.beneficiario_nome}",
                contraparte=item.conta_pagar.beneficiario_nome,
                origem="Conta a pagar", origem_codigo="pagar", registro_id=item.pk,
                documento=item.conta_pagar.documento_numero or item.referencia_pagamento,
                forma_pagamento=item.forma_pagamento.descricao if item.forma_pagamento else "Nao informada",
                saida=item.valor_liquido,
                referencia_url=f'{reverse("financeiro:pagar_detail", args=[item.conta_pagar_id])}?pagamento={item.pk}',
            ))

        try:
            from apps.pdv.models import PagamentoVendaPDV
        except Exception:
            PagamentoVendaPDV = None
        if PagamentoVendaPDV:
            vendas = PagamentoVendaPDV.objects.filter(
                Q(data_liquidacao_prevista=self.data)
                | Q(data_liquidacao_prevista__isnull=True, venda_pdv__data_venda__date=self.data),
                venda_pdv__filial=self.filial,
                forma_pagamento__movimenta_caixa=True,
            ).exclude(
                forma_pagamento__tipo__in=("boleto", "vale"),
            ).exclude(venda_pdv__status="cancelada").select_related(
                "venda_pdv__cliente", "forma_pagamento", "forma_pagamento__conta_bancaria_padrao", "conta_bancaria",
            )
            for item in vendas:
                conta = item.conta_bancaria or item.forma_pagamento.conta_bancaria_padrao
                valor = item.valor_entrada_liquida
                if not conta or conta.pk not in self.conta_ids or valor <= ZERO:
                    continue
                cliente = str(item.venda_pdv.cliente) if item.venda_pdv.cliente else "Consumidor final"
                movimentos.append(MovimentoDiario(
                    data=item.data_liquidacao_prevista or timezone.localtime(item.venda_pdv.data_venda).date(), conta=conta,
                    descricao=f"Venda #{item.venda_pdv.numero_venda} - {cliente}", contraparte=cliente,
                    origem="Venda PDV", origem_codigo="venda", registro_id=item.pk,
                    documento=str(item.venda_pdv.numero_venda), forma_pagamento=item.forma_pagamento.descricao,
                    entrada=valor, valor_bruto=item.valor_bruto_recebido,
                    valor_taxa=item.valor_taxa if item.taxa_calculada_em else ZERO,
                ))
        return sorted(movimentos, key=lambda m: (m.origem, m.descricao.casefold(), m.registro_id))

    def _saldos_antes_do_dia(self):
        saldos = defaultdict(lambda: ZERO)
        manuais = ExtratoBancario.objects.filter(
            filial=self.filial, conta_bancaria_id__in=self.conta_ids, data_lancamento__lt=self.data,
        ).exclude(status="excluido").values_list("conta_bancaria_id", "valor")
        for conta_id, valor in manuais.iterator():
            _somar(saldos, conta_id, valor)
        recebimentos = ContaReceber.objects.filter(
            filial=self.filial, conta_bancaria_id__in=self.conta_ids,
            valor_pago__gt=0,
        ).filter(
            Q(data_liquidacao_prevista__lt=self.data)
            | Q(data_liquidacao_prevista__isnull=True, data_pagamento__lt=self.data)
        )
        for item in recebimentos.iterator():
            _somar(saldos, item.conta_bancaria_id, item.valor_entrada_liquida)
        pagamentos = PagamentoContaPagar.objects.filter(
            filial=self.filial, conta_bancaria_id__in=self.conta_ids,
            data_pagamento__lt=self.data, conta_pagar__excluido_em__isnull=True,
        ).values_list("conta_bancaria_id", "valor_pago", "valor_juros", "valor_multa", "valor_desconto")
        for conta_id, pago, juros, multa, desconto in pagamentos.iterator():
            _somar(saldos, conta_id, -((pago or ZERO) + (juros or ZERO) + (multa or ZERO) - (desconto or ZERO)))
        try:
            from apps.pdv.models import PagamentoVendaPDV
            vendas = PagamentoVendaPDV.objects.filter(
                Q(data_liquidacao_prevista__lt=self.data)
                | Q(data_liquidacao_prevista__isnull=True, venda_pdv__data_venda__date__lt=self.data),
                venda_pdv__filial=self.filial,
                forma_pagamento__movimenta_caixa=True,
            ).exclude(
                forma_pagamento__tipo__in=("boleto", "vale"),
            ).exclude(venda_pdv__status="cancelada").select_related(
                "forma_pagamento__conta_bancaria_padrao", "conta_bancaria",
            )
            for item in vendas.iterator():
                conta = item.conta_bancaria or item.forma_pagamento.conta_bancaria_padrao
                valor = item.valor_entrada_liquida
                if conta and conta.pk in self.conta_ids and valor > ZERO:
                    _somar(saldos, conta.pk, valor)
        except Exception:
            pass
        return saldos

    def _pendencias_sem_conta_do_dia(self):
        itens = []
        for item in ContaReceber.objects.filter(
            filial=self.filial, valor_pago__gt=0, conta_bancaria__isnull=True,
        ).filter(
            Q(data_liquidacao_prevista=self.data)
            | Q(data_liquidacao_prevista__isnull=True, data_pagamento=self.data)
        ).select_related("cliente"):
            itens.append({"descricao": f"Recebimento - {item.cliente}", "valor": item.valor_entrada_liquida, "tipo": "entrada"})
        for item in PagamentoContaPagar.objects.filter(
            filial=self.filial, data_pagamento=self.data, conta_bancaria__isnull=True,
            conta_pagar__excluido_em__isnull=True,
        ).select_related("conta_pagar__fornecedor", "conta_pagar__funcionario"):
            itens.append({"descricao": f"Pagamento - {item.conta_pagar.beneficiario_nome}", "valor": item.valor_liquido, "tipo": "saida"})
        try:
            from apps.pdv.models import PagamentoVendaPDV
            vendas = PagamentoVendaPDV.objects.filter(
                Q(data_liquidacao_prevista=self.data)
                | Q(data_liquidacao_prevista__isnull=True, venda_pdv__data_venda__date=self.data),
                venda_pdv__filial=self.filial,
                forma_pagamento__movimenta_caixa=True,
                conta_bancaria__isnull=True,
                forma_pagamento__conta_bancaria_padrao__isnull=True,
            ).exclude(
                forma_pagamento__tipo__in=("boleto", "vale"),
            ).exclude(venda_pdv__status="cancelada").select_related("venda_pdv", "forma_pagamento")
            for item in vendas:
                valor = item.valor_entrada_liquida
                if valor:
                    itens.append({
                        "descricao": f"Venda #{item.venda_pdv.numero_venda} - {item.forma_pagamento.descricao}",
                        "valor": valor,
                        "tipo": "entrada",
                    })
        except Exception:
            pass
        return itens

    def _recebimentos_previstos(self):
        """Mostra creditos futuros sem mistura-los ao saldo realizado."""
        try:
            from apps.pdv.models import PagamentoVendaPDV
        except Exception:
            return []
        limite = self.data + timedelta(days=45)
        vendas = PagamentoVendaPDV.objects.filter(
            venda_pdv__filial=self.filial,
            data_liquidacao_prevista__gt=self.data,
            data_liquidacao_prevista__lte=limite,
            forma_pagamento__movimenta_caixa=True,
        ).exclude(
            forma_pagamento__tipo__in=("boleto", "vale"),
        ).exclude(venda_pdv__status="cancelada").select_related(
            "venda_pdv__cliente", "forma_pagamento", "forma_pagamento__conta_bancaria_padrao", "conta_bancaria",
        ).order_by("data_liquidacao_prevista", "pk")
        itens = []
        for item in vendas:
            conta = item.conta_bancaria or item.forma_pagamento.conta_bancaria_padrao
            cliente = str(item.venda_pdv.cliente) if item.venda_pdv.cliente else "Consumidor final"
            itens.append({
                "data": item.data_liquidacao_prevista,
                "descricao": f"Venda #{item.venda_pdv.numero_venda} - {cliente}",
                "forma": item.forma_pagamento.descricao,
                "conta": conta.descricao if conta else "Conta nao definida",
                "bandeira": item.bandeira,
                "parcelas": item.numero_parcelas,
                "valor_bruto": item.valor_bruto_recebido,
                "valor_taxa": item.valor_taxa,
                "valor_liquido": item.valor_entrada_liquida,
            })
        recebimentos = ContaReceber.objects.filter(
            filial=self.filial,
            status__in=("aberto", "vencido", "negociado"),
            data_vencimento__lte=limite,
            valor_saldo__gt=0,
        ).select_related("cliente", "forma_pagamento", "conta_bancaria")
        for item in recebimentos:
            forma = item.forma_pagamento
            prazo = forma.prazo_compensacao_dias_uteis if forma else 0
            data_prevista = adicionar_dias_uteis_bancarios(item.data_vencimento, prazo, self.filial)
            if data_prevista <= self.data:
                continue
            calculo = forma.calcular_taxa_recebimento(item.valor_saldo, item.total_parcelas) if forma else {
                "taxa": ZERO, "liquido": item.valor_saldo,
            }
            itens.append({
                "data": data_prevista,
                "descricao": f"Conta a receber - {item.cliente}",
                "forma": forma.descricao if forma else "Nao informada",
                "conta": item.conta_bancaria.descricao if item.conta_bancaria else "Conta nao definida",
                "bandeira": "",
                "parcelas": item.total_parcelas,
                "valor_bruto": item.valor_saldo,
                "valor_taxa": calculo["taxa"],
                "valor_liquido": calculo["liquido"],
            })
        itens.sort(key=lambda item: (item["data"], item["descricao"].casefold()))
        return itens
