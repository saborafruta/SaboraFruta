from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.financeiro.models import ContaBancaria, ContaPagar
from apps.financeiro.models.extrato import ExtratoBancario
from apps.financeiro.constants.enums import StatusContaReceber
from apps.financeiro.models.receber_pagar import ContaReceber, PagamentoContaPagar, PagamentoContaReceber
from apps.core.services.calendario import adicionar_dias_uteis_bancarios


ZERO = Decimal("0")


@dataclass
class MovimentoDiario:
    data: date
    conta: ContaBancaria | None
    descricao: str
    contraparte: str
    origem: str
    origem_codigo: str
    registro_id: int
    documento: str = ""
    forma_pagamento: str = "Sem forma vinculada"
    entrada: Decimal = ZERO
    saida: Decimal = ZERO
    valor_bruto: Decimal = ZERO
    valor_taxa: Decimal = ZERO
    taxa_percentual: Decimal = ZERO
    taxa_fixa: Decimal = ZERO
    taxa_descontada: bool = False
    taxa_em_pagamento: bool = False
    referencia_url: str = ""
    excluido: bool = False
    momento: datetime | None = None
    despesa_pessoal: bool = False
    classificacao: str = ""
    editavel: bool = True
    bandeira: str = ""
    numero_parcelas: int | None = None
    data_credito: date | None = None
    op_url: str = ""
    venda_pdv_id: int | None = None
    transferencia: bool = False
    categoria_id: int | None = None
    fornecedor_id: int | None = None
    funcionario_id: int | None = None

    @property
    def valor(self):
        return self.entrada - self.saida

    @property
    def entrada_bruta(self):
        if self.taxa_descontada and self.valor_bruto > ZERO:
            return self.valor_bruto
        return self.entrada

    @property
    def valor_final_taxa(self):
        if self.taxa_em_pagamento:
            return self.valor_bruto
        return self.entrada

    @property
    def hora(self):
        return timezone.localtime(self.momento).strftime("%H:%M") if self.momento else "--:--"


def _somar(destino, conta_id, valor):
    if conta_id:
        destino[conta_id] += valor or ZERO


def _eh_despesa_pessoal(plano_contas):
    atual = plano_contas
    visitados = set()
    while atual and atual.pk not in visitados:
        visitados.add(atual.pk)
        if atual.despesa_pessoal:
            return True
        atual = atual.conta_pai
    return False


def _contexto_titulo_receber(titulo):
    """Traduz a origem contábil para a origem comercial exibida ao usuário."""
    cliente = str(titulo.cliente)
    classificacao = (
        titulo.plano_contas.descricao
        if titulo.plano_contas_id else "Conta a receber"
    )
    if titulo.documento_tipo == "pedido_moda" and titulo.documento_id:
        numero = titulo.documento_numero or str(titulo.documento_id)
        numero_exibicao = f"{int(numero):06d}" if str(numero).isdigit() else str(numero)
        return {
            "descricao": f"Venda OP #{numero_exibicao} - {cliente}",
            "origem": "Venda",
            "classificacao": "Venda de OP",
            "op_url": reverse("moda:op2-detail", args=[titulo.documento_id]),
        }
    return {
        "descricao": f"Recebimento de {cliente}",
        "origem": "Conta a receber",
        "classificacao": classificacao,
        "op_url": "",
    }


class PosicaoDiariaCaixaService:
    """Consolida o livro-caixa operacional sem misturar valores projetados."""

    CORES = ("azul", "verde", "ambar", "violeta", "cinza")

    def __init__(self, filial, data_referencia, data_inicio=None):
        self.filial = filial
        self.data = data_referencia
        self.data_inicio = data_inicio or data_referencia
        self.data_fim = data_referencia
        self.contas = list(
            ContaBancaria.objects.for_filial(filial)
            .filter(ativo=True)
            .order_by("descricao", "banco_nome", "pk")
        )
        self.conta_ids = {conta.pk for conta in self.contas}

    def gerar(
        self, *, incluir_excluidos=False, incluir_previstos=False,
        previsao_inicio=None, previsao_fim=None, conta_filtro=None, ordem="horario",
        categoria_filtro=None, fornecedor_filtro=None, funcionario_filtro=None,
    ):
        movimentos = self._movimentos_do_dia(incluir_excluidos=incluir_excluidos)
        movimentos_ativos = [mov for mov in movimentos if not mov.excluido]
        movimentos_operacionais = [mov for mov in movimentos_ativos if not mov.transferencia]
        movimentos_exibidos = movimentos_operacionais
        if categoria_filtro:
            movimentos_exibidos = [
                mov for mov in movimentos_exibidos if mov.categoria_id == categoria_filtro
            ]
        if fornecedor_filtro:
            movimentos_exibidos = [
                mov for mov in movimentos_exibidos if mov.fornecedor_id == fornecedor_filtro
            ]
        if funcionario_filtro:
            movimentos_exibidos = [
                mov for mov in movimentos_exibidos if mov.funcionario_id == funcionario_filtro
            ]
        if conta_filtro:
            movimentos_exibidos = [
                mov for mov in movimentos_exibidos
                if mov.conta and mov.conta.pk == conta_filtro
            ]
        if ordem == "conta":
            movimentos_exibidos = sorted(
                movimentos_exibidos,
                key=lambda mov: (
                    (
                        (mov.conta.descricao or mov.conta.banco_nome or "").casefold()
                        if mov.conta else "zz conta nao definida"
                    ),
                    -(mov.momento.timestamp() if mov.momento else 0),
                ),
            )
        elif ordem == "forma":
            movimentos_exibidos = sorted(
                movimentos_exibidos,
                key=lambda mov: (
                    (mov.forma_pagamento or "").casefold(),
                    -(mov.momento.timestamp() if mov.momento else 0),
                ),
            )
        else:
            movimentos_exibidos = sorted(
                movimentos_exibidos,
                key=lambda mov: (
                    mov.data,
                    timezone.localtime(mov.momento).time() if mov.momento else time.min,
                    mov.registro_id,
                ),
                reverse=True,
            )
        entradas = [mov for mov in movimentos_exibidos if mov.entrada > ZERO]
        saidas = [mov for mov in movimentos_exibidos if mov.saida > ZERO]
        entradas_operacionais = [mov for mov in movimentos_operacionais if mov.entrada > ZERO]
        saidas_operacionais = [mov for mov in movimentos_operacionais if mov.saida > ZERO]
        filtros_analiticos = bool(categoria_filtro or fornecedor_filtro or funcionario_filtro)
        movimentos_para_taxas = movimentos_exibidos if filtros_analiticos else movimentos_ativos
        entradas_com_taxa = [mov for mov in movimentos_para_taxas if mov.entrada > ZERO]
        saldo_anterior = self._saldos_antes_do_dia()
        por_conta_dia = defaultdict(lambda: ZERO)
        for mov in movimentos_ativos:
            if mov.conta:
                por_conta_dia[mov.conta.pk] += (
                    mov.valor - mov.valor_taxa if mov.taxa_em_pagamento else mov.valor
                )

        contas = []
        for indice, conta in enumerate(self.contas):
            abertura = (conta.saldo_inicial or ZERO) + saldo_anterior[conta.pk]
            fechamento = abertura + por_conta_dia[conta.pk]
            nome_base = " ".join(filter(None, [conta.descricao, conta.banco_nome, conta.tipo_conta])).casefold()
            eh_dinheiro = "dinheiro" in nome_base or "caixa" in nome_base
            conta.posicao_abertura = abertura
            entradas_conta = [
                m for m in entradas_operacionais if m.conta and m.conta.pk == conta.pk
            ]
            conta.posicao_entradas = sum((m.entrada for m in entradas_conta), ZERO)
            conta.posicao_saidas = sum(
                (
                    m.saida for m in saidas_operacionais
                    if m.conta and m.conta.pk == conta.pk
                ),
                ZERO,
            )
            conta.posicao_fechamento = fechamento
            conta.posicao_cor = "azul" if eh_dinheiro else self.CORES[indice % len(self.CORES)]
            conta.eh_dinheiro = eh_dinheiro
            contas.append(conta)

        total_abertura = sum((c.posicao_abertura for c in contas), ZERO)
        # A transferencia nao aparece como entrada/saida operacional, mas sua
        # tarifa continua sendo uma despesa real que reduz o saldo consolidado.
        total_taxas_entradas = sum((m.valor_taxa for m in entradas_com_taxa), ZERO)
        total_taxas_transferencias = sum(
            (m.valor_taxa for m in entradas_com_taxa if m.transferencia), ZERO
        )
        total_entradas = sum((m.entrada for m in entradas), ZERO)
        total_liquido_entradas = total_entradas
        transacoes_taxas = [
            movimento for movimento in entradas_com_taxa
            if movimento.forma_pagamento != "Sem forma vinculada"
        ]
        taxas_pagamentos = [movimento for movimento in saidas if movimento.taxa_em_pagamento]
        detalhes_taxas = transacoes_taxas + taxas_pagamentos
        total_taxas_pagamentos = sum((m.valor_taxa for m in taxas_pagamentos), ZERO)
        total_taxas_transacoes = total_taxas_entradas + total_taxas_pagamentos
        total_bruto_transacoes_taxas = sum((m.entrada_bruta for m in transacoes_taxas), ZERO)
        total_liquido_transacoes_taxas = sum((m.entrada for m in transacoes_taxas), ZERO)
        total_saidas_bancarias = sum((m.saida for m in saidas), ZERO)
        total_saidas = (
            total_saidas_bancarias
            + total_taxas_entradas
            + total_taxas_pagamentos
        )
        total_fechamento = sum((c.posicao_fechamento for c in contas), ZERO)
        total_despesas_pessoais = sum((m.saida for m in saidas if m.despesa_pessoal), ZERO)
        taxas_por_forma = self._agrupar_taxas(entradas)
        previsoes = self._recebimentos_previstos(
            previsao_inicio or self.data,
            previsao_fim or (self.data + timedelta(days=7)),
        ) if incluir_previstos else []
        entradas_realizadas = {(item.origem_codigo, item.registro_id) for item in entradas}
        previsoes = [
            item for item in previsoes
            if (item["origem_codigo"], item["registro_id"]) not in entradas_realizadas
        ]
        previstos_por_conta = defaultdict(lambda: ZERO)
        for item in previsoes:
            _somar(previstos_por_conta, item.get("conta_id"), item["valor_liquido"])
        for conta in contas:
            conta.posicao_prevista_entrada = previstos_por_conta[conta.pk]
            conta.posicao_saldo_projetado = (
                conta.posicao_fechamento + conta.posicao_prevista_entrada
            )
        return {
            "contas": contas,
            "entradas": entradas,
            "saidas": saidas,
            "extrato": movimentos_exibidos,
            "excluidos": [mov for mov in movimentos if mov.excluido],
            "total_abertura": total_abertura,
            "total_entradas": total_entradas,
            "total_liquido_entradas": total_liquido_entradas,
            "total_bruto_transacoes_taxas": total_bruto_transacoes_taxas,
            "total_liquido_transacoes_taxas": total_liquido_transacoes_taxas,
            "total_saidas": total_saidas,
            "total_saidas_bancarias": total_saidas_bancarias,
            "total_fechamento": total_fechamento,
            "total_despesas_pessoais": total_despesas_pessoais,
            "total_taxas_entradas": total_taxas_entradas,
            "total_taxas_pagamentos": total_taxas_pagamentos,
            "total_taxas_transacoes": total_taxas_transacoes,
            "transacoes_taxas": transacoes_taxas,
            "taxas_pagamentos": taxas_pagamentos,
            "detalhes_taxas": detalhes_taxas,
            "taxas_por_forma": taxas_por_forma,
            # Taxas de recebimentos ja foram abatidas das entradas liquidas.
            # Tarifas de pagamentos sao cobrancas adicionais e reduzem o caixa.
            # Transferencias nao sao exibidas como entradas/saidas operacionais.
            # A tarifa, porem, reduz de fato o caixa consolidado e precisa aparecer
            # no resultado do dia para reconciliar com os saldos das contas.
            "variacao_dia": (
                total_entradas
                - total_saidas_bancarias
                - total_taxas_pagamentos
                - total_taxas_transferencias
            ),
            "totais_forma_entrada": self._agrupar(entradas, "forma_pagamento", "entrada"),
            "totais_forma_saida": self._agrupar(saidas, "forma_pagamento", "saida"),
            "totais_conta_entrada": self._agrupar(entradas, "conta", "entrada"),
            "totais_conta_saida": self._agrupar(saidas, "conta", "saida"),
            "sem_conta": self._pendencias_sem_conta_do_dia(),
            "data_inicio": self.data_inicio,
            "data_fim": self.data_fim,
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
                chave = (
                    chave.descricao or chave.banco_nome or f"Conta #{chave.pk}"
                ) if chave else "Conta nao definida"
            totais[chave or "Sem forma vinculada"] += getattr(movimento, campo_valor)
        return [{"nome": nome, "valor": valor} for nome, valor in sorted(
            totais.items(), key=lambda item: (-item[1], item[0].casefold())
        )]

    @classmethod
    def _agrupar_com_taxas(cls, saidas, entradas, atributo):
        totais = defaultdict(lambda: ZERO)
        for item in cls._agrupar(saidas, atributo, "saida"):
            totais[item["nome"]] += item["valor"]
        for movimento in entradas:
            chave = getattr(movimento, atributo)
            if atributo == "conta":
                chave = (
                    chave.descricao or chave.banco_nome or f"Conta #{chave.pk}"
                ) if chave else "Conta nao definida"
            totais[chave or "Sem forma vinculada"] += movimento.valor_taxa
        return [{"nome": nome, "valor": valor} for nome, valor in sorted(
            totais.items(), key=lambda item: (-item[1], item[0].casefold())
        )]

    @staticmethod
    def _agrupar_taxas(movimentos):
        grupos = {}
        for movimento in movimentos:
            if movimento.forma_pagamento == "Sem forma vinculada":
                continue
            nome = movimento.forma_pagamento or "Sem forma vinculada"
            item = grupos.setdefault(nome, {
                "nome": nome, "valor": ZERO, "bruto": ZERO, "quantidade": 0,
                "percentual": ZERO, "fixa": ZERO, "descontada": False,
            })
            item["valor"] += movimento.valor_taxa
            item["bruto"] += movimento.valor_bruto or movimento.entrada
            item["quantidade"] += 1
            item["percentual"] = max(item["percentual"], movimento.taxa_percentual)
            item["fixa"] = max(item["fixa"], movimento.taxa_fixa)
            item["descontada"] = item["descontada"] or movimento.taxa_descontada
        for item in grupos.values():
            item["percentual_efetivo"] = (
                item["valor"] / item["bruto"] * Decimal("100") if item["bruto"] else ZERO
            )
        return sorted(grupos.values(), key=lambda item: (-item["valor"], item["nome"].casefold()))

    def _movimentos_do_dia(self, *, incluir_excluidos=False):
        movimentos = []
        manuais = ExtratoBancario.objects.filter(
            filial=self.filial, conta_bancaria_id__in=self.conta_ids,
        ).filter(
            Q(data_credito__range=(self.data_inicio, self.data_fim))
            | Q(
                data_credito__isnull=True,
                data_lancamento__range=(self.data_inicio, self.data_fim),
            ),
        ).select_related(
            "conta_bancaria", "forma_pagamento", "plano_contas",
            "plano_contas__conta_pai", "plano_contas__conta_pai__conta_pai",
        )
        if not incluir_excluidos:
            manuais = manuais.exclude(status="excluido")
        for item in manuais:
            valor = item.valor or ZERO
            taxa_descontada = bool(valor > ZERO and item.taxa_calculada_em)
            entrada = item.valor_entrada_liquida if taxa_descontada else max(valor, ZERO)
            movimentos.append(MovimentoDiario(
                data=item.data_credito or item.data_lancamento, conta=item.conta_bancaria,
                descricao=item.historico or "Lancamento manual", contraparte="Movimento manual",
                origem="Manual" if item.origem == "manual" else "Extrato bancario",
                origem_codigo="manual", registro_id=item.pk, documento=item.documento,
                forma_pagamento=(
                    item.forma_pagamento.descricao if item.forma_pagamento else "Sem forma vinculada"
                ),
                taxa_percentual=(
                    item.taxa_percentual_aplicada if taxa_descontada
                    else (item.forma_pagamento.taxa_administrativa if item.forma_pagamento else ZERO)
                ),
                taxa_fixa=(
                    item.taxa_fixa_aplicada if taxa_descontada
                    else (item.forma_pagamento.taxa_fixa if item.forma_pagamento else ZERO)
                ),
                entrada=entrada, saida=abs(min(valor, ZERO)),
                valor_bruto=max(valor, ZERO),
                valor_taxa=item.valor_taxa if taxa_descontada else ZERO,
                taxa_descontada=taxa_descontada,
                excluido=item.status == "excluido",
                momento=item.created_at,
                classificacao=(
                    item.plano_contas.descricao
                    if item.plano_contas_id else ("Credito manual" if valor > ZERO else "Saida manual")
                ),
                despesa_pessoal=bool(valor < ZERO and _eh_despesa_pessoal(item.plano_contas)),
                editavel=valor > ZERO,
                bandeira=item.bandeira,
                numero_parcelas=item.numero_parcelas,
                data_credito=item.data_credito,
                transferencia=item.tipo_lancamento == "transferencia",
                categoria_id=item.plano_contas_id,
            ))

        recebimentos = PagamentoContaReceber.objects.filter(
            filial=self.filial,
            data_pagamento__lte=self.data_fim,
            conta_receber__excluido_em__isnull=True,
            conta_receber__status__in=[
                StatusContaReceber.PAGO,
                StatusContaReceber.PAGO_PARCIAL,
                StatusContaReceber.VENCIDO,
                StatusContaReceber.NEGOCIADO,
                StatusContaReceber.ABERTO,
            ],
        ).select_related(
            "conta_bancaria", "conta_receber__cliente", "forma_pagamento",
            "forma_pagamento__conta_bancaria_padrao", "conta_receber__plano_contas",
        )
        for item in recebimentos:
            bruto = item.valor_pago or ZERO
            taxa = item.valor_taxa or ZERO
            liquido = item.valor_liquido if (item.valor_liquido or taxa) else max(bruto - taxa, ZERO)
            forma = item.forma_pagamento
            # Uma baixa confirma que o recebimento ocorreu. O prazo da forma
            # serve para projetar valores ainda em aberto, mas não pode mover
            # uma baixa já registrada para outro dia nem mantê-la no contas a
            # receber da posição diária.
            data_movimento = item.data_pagamento
            if not self.data_inicio <= data_movimento <= self.data_fim:
                continue
            conta = item.conta_bancaria or (
                forma.conta_bancaria_padrao if forma else None
            )
            if conta and conta.pk not in self.conta_ids:
                conta = None
            titulo = item.conta_receber
            contexto_titulo = _contexto_titulo_receber(titulo)
            movimentos.append(MovimentoDiario(
                data=data_movimento, conta=conta,
                descricao=contexto_titulo["descricao"], contraparte=str(titulo.cliente),
                origem=contexto_titulo["origem"], origem_codigo="receber", registro_id=titulo.pk,
                documento=titulo.documento_numero,
                forma_pagamento=forma.descricao if forma else "Sem forma vinculada",
                entrada=liquido, valor_bruto=bruto, valor_taxa=taxa,
                taxa_percentual=(
                    forma.calcular_taxa_recebimento(bruto, item.numero_parcelas or 1, item.bandeira)["percentual"]
                    if forma else ZERO
                ),
                taxa_fixa=(
                    forma.calcular_taxa_recebimento(bruto, item.numero_parcelas or 1, item.bandeira)["fixa"]
                    if forma else ZERO
                ),
                taxa_descontada=taxa > ZERO,
                referencia_url=reverse("financeiro:receber_detail", args=[titulo.pk]),
                momento=item.updated_at,
                classificacao=contexto_titulo["classificacao"],
                bandeira=item.bandeira,
                numero_parcelas=item.numero_parcelas,
                op_url=contexto_titulo["op_url"],
                categoria_id=titulo.plano_contas_id,
            ))

        pagamentos = PagamentoContaPagar.objects.filter(
            filial=self.filial, conta_bancaria_id__in=self.conta_ids,
            data_pagamento__range=(self.data_inicio, self.data_fim), conta_pagar__excluido_em__isnull=True,
        ).exclude(conta_pagar__documento_tipo__startswith="taxa_").select_related(
            "conta_bancaria", "forma_pagamento", "conta_pagar__fornecedor", "conta_pagar__funcionario",
            "conta_pagar__plano_contas", "conta_pagar__plano_contas__conta_pai",
            "conta_pagar__plano_contas__conta_pai__conta_pai",
        )
        pagamentos_ids = [item.pk for item in pagamentos]
        tarifas_legadas = {
            conta.documento_id: conta.valor_pago or conta.valor_final or ZERO
            for conta in ContaPagar.all_objects.filter(
                filial=self.filial,
                documento_tipo="taxa_pagamento",
                documento_id__in=pagamentos_ids,
                excluido_em__isnull=True,
            )
        }
        for item in pagamentos:
            tarifa_pagamento = item.tarifa_bancaria
            if tarifa_pagamento is None:
                tarifa_pagamento = tarifas_legadas.get(item.pk)
            if tarifa_pagamento is None:
                tarifa_pagamento = (
                    item.forma_pagamento.tarifa_pagamento_fixa
                    if item.forma_pagamento_id else ZERO
                )
            tarifa_pagamento = tarifa_pagamento or ZERO
            movimentos.append(MovimentoDiario(
                data=item.data_pagamento, conta=item.conta_bancaria,
                descricao=item.conta_pagar.descricao_exibicao,
                contraparte=item.conta_pagar.beneficiario_nome,
                origem="Conta a pagar", origem_codigo="pagar", registro_id=item.pk,
                documento=item.conta_pagar.documento_numero or item.referencia_pagamento,
                forma_pagamento=item.forma_pagamento.descricao if item.forma_pagamento else "Sem forma vinculada",
                saida=item.valor_liquido,
                valor_bruto=item.valor_liquido,
                valor_taxa=tarifa_pagamento,
                taxa_fixa=tarifa_pagamento,
                taxa_em_pagamento=tarifa_pagamento > ZERO,
                referencia_url=f'{reverse("financeiro:pagar_detail", args=[item.conta_pagar_id])}?pagamento={item.pk}',
                momento=item.created_at,
                despesa_pessoal=_eh_despesa_pessoal(item.conta_pagar.plano_contas),
                classificacao=(
                    item.conta_pagar.plano_contas.descricao
                    if item.conta_pagar.plano_contas_id else "Despesa sem classificacao"
                ),
                categoria_id=item.conta_pagar.plano_contas_id,
                fornecedor_id=item.conta_pagar.fornecedor_id,
                funcionario_id=item.conta_pagar.funcionario_id,
            ))

        try:
            from apps.pdv.models import PagamentoVendaPDV
        except Exception:
            PagamentoVendaPDV = None
        if PagamentoVendaPDV:
            vendas = PagamentoVendaPDV.objects.filter(
                Q(data_liquidacao_prevista__range=(self.data_inicio, self.data_fim))
                | Q(data_liquidacao_prevista__isnull=True, venda_pdv__data_venda__date__range=(self.data_inicio, self.data_fim)),
                venda_pdv__filial=self.filial,
                forma_pagamento__movimenta_caixa=True,
            ).exclude(
                forma_pagamento__tipo__in=("boleto", "vale"),
            ).exclude(venda_pdv__status="cancelada").select_related(
                "venda_pdv__cliente", "forma_pagamento", "forma_pagamento__conta_bancaria_padrao", "conta_bancaria",
            )
            for item in vendas:
                conta = item.conta_bancaria or item.forma_pagamento.conta_bancaria_padrao
                if conta and conta.pk not in self.conta_ids:
                    conta = None
                valor = item.valor_entrada_liquida
                if valor <= ZERO:
                    continue
                cliente = str(item.venda_pdv.cliente) if item.venda_pdv.cliente else "Consumidor final"
                movimentos.append(MovimentoDiario(
                    data=item.data_liquidacao_prevista or timezone.localtime(item.venda_pdv.data_venda).date(), conta=conta,
                    descricao=f"Venda #{item.venda_pdv.numero_venda} - {cliente}", contraparte=cliente,
                    origem="Venda PDV", origem_codigo="venda", registro_id=item.pk,
                    venda_pdv_id=item.venda_pdv_id,
                    documento=str(item.venda_pdv.numero_venda), forma_pagamento=item.forma_pagamento.descricao,
                    entrada=valor, valor_bruto=item.valor_bruto_recebido,
                    valor_taxa=item.valor_taxa if item.taxa_calculada_em else ZERO,
                    taxa_percentual=item.taxa_percentual_aplicada if item.taxa_calculada_em else ZERO,
                    taxa_fixa=item.taxa_fixa_aplicada if item.taxa_calculada_em else ZERO,
                    taxa_descontada=bool(item.taxa_calculada_em),
                    momento=item.created_at,
                    classificacao="Venda",
                    bandeira=item.bandeira,
                    numero_parcelas=item.numero_parcelas,
                ))
        return sorted(movimentos, key=lambda m: (m.origem, m.descricao.casefold(), m.registro_id))

    def _saldos_antes_do_dia(self):
        saldos = defaultdict(lambda: ZERO)
        manuais = ExtratoBancario.objects.filter(
            filial=self.filial, conta_bancaria_id__in=self.conta_ids,
        ).filter(
            Q(data_credito__lt=self.data_inicio)
            | Q(data_credito__isnull=True, data_lancamento__lt=self.data_inicio),
        ).exclude(status="excluido")
        for item in manuais.iterator():
            valor = item.valor_entrada_liquida if item.valor > ZERO else item.valor
            _somar(saldos, item.conta_bancaria_id, valor)
        recebimentos = PagamentoContaReceber.objects.filter(
            filial=self.filial,
            data_pagamento__lt=self.data_inicio,
            conta_receber__excluido_em__isnull=True,
        ).select_related("conta_bancaria", "forma_pagamento__conta_bancaria_padrao")
        for item in recebimentos.iterator():
            conta = item.conta_bancaria or (
                item.forma_pagamento.conta_bancaria_padrao
                if item.forma_pagamento_id else None
            )
            if conta and conta.pk in self.conta_ids:
                liquido = item.valor_liquido if (item.valor_liquido or item.valor_taxa) else max((item.valor_pago or ZERO) - (item.valor_taxa or ZERO), ZERO)
                _somar(saldos, conta.pk, liquido)
        pagamentos = PagamentoContaPagar.objects.filter(
            filial=self.filial, conta_bancaria_id__in=self.conta_ids,
            data_pagamento__lt=self.data_inicio, conta_pagar__excluido_em__isnull=True,
        ).exclude(conta_pagar__documento_tipo__startswith="taxa_").select_related(
            "forma_pagamento",
        )
        pagamentos_ids = list(pagamentos.values_list("pk", flat=True))
        tarifas_legadas = {
            conta.documento_id: conta.valor_pago or conta.valor_final or ZERO
            for conta in ContaPagar.all_objects.filter(
                filial=self.filial,
                documento_tipo="taxa_pagamento",
                documento_id__in=pagamentos_ids,
                excluido_em__isnull=True,
            )
        }
        for pagamento in pagamentos.iterator():
            tarifa = pagamento.tarifa_bancaria
            if tarifa is None:
                tarifa = tarifas_legadas.get(pagamento.pk)
            if tarifa is None:
                tarifa = (
                    pagamento.forma_pagamento.tarifa_pagamento_fixa
                    if pagamento.forma_pagamento_id else ZERO
                )
            _somar(
                saldos,
                pagamento.conta_bancaria_id,
                -((pagamento.valor_pago or ZERO) + (tarifa or ZERO)),
            )
        try:
            from apps.pdv.models import PagamentoVendaPDV
            vendas = PagamentoVendaPDV.objects.filter(
                Q(data_liquidacao_prevista__lt=self.data_inicio)
                | Q(data_liquidacao_prevista__isnull=True, venda_pdv__data_venda__date__lt=self.data_inicio),
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
        for item in PagamentoContaReceber.objects.filter(
            filial=self.filial,
            data_pagamento__lte=self.data_fim,
            conta_receber__excluido_em__isnull=True,
            conta_bancaria__isnull=True,
            forma_pagamento__conta_bancaria_padrao__isnull=True,
        ).select_related("conta_receber__cliente", "forma_pagamento"):
            data_movimento = item.data_pagamento
            if self.data_inicio <= data_movimento <= self.data_fim:
                liquido = item.valor_liquido if (item.valor_liquido or item.valor_taxa) else max((item.valor_pago or ZERO) - (item.valor_taxa or ZERO), ZERO)
                itens.append({"descricao": f"Recebimento - {item.conta_receber.cliente}", "valor": liquido, "tipo": "entrada"})
        for item in PagamentoContaPagar.objects.filter(
            filial=self.filial, data_pagamento__range=(self.data_inicio, self.data_fim), conta_bancaria__isnull=True,
            conta_pagar__excluido_em__isnull=True,
        ).exclude(conta_pagar__documento_tipo__startswith="taxa_").select_related(
            "conta_pagar__fornecedor", "conta_pagar__funcionario",
        ):
            itens.append({"descricao": item.conta_pagar.descricao_exibicao, "valor": item.valor_liquido, "tipo": "saida"})
        try:
            from apps.pdv.models import PagamentoVendaPDV
            vendas = PagamentoVendaPDV.objects.filter(
                Q(data_liquidacao_prevista__range=(self.data_inicio, self.data_fim))
                | Q(data_liquidacao_prevista__isnull=True, venda_pdv__data_venda__date__range=(self.data_inicio, self.data_fim)),
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

    def _recebimentos_previstos(self, data_inicio, data_fim):
        """Mostra creditos futuros sem mistura-los ao saldo realizado."""
        # A condição de atraso acompanha o dia que está sendo conferido, não o
        # relógio do servidor. Isso mantém a posição histórica coerente.
        hoje = self.data_fim
        try:
            from apps.pdv.models import PagamentoVendaPDV
        except Exception:
            return []
        vendas = PagamentoVendaPDV.objects.filter(
            venda_pdv__filial=self.filial,
            data_liquidacao_prevista__range=(data_inicio, data_fim),
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
                "classificacao": "Venda",
                "forma": item.forma_pagamento.descricao,
                "conta": conta.descricao if conta else "Conta nao definida",
                "conta_id": conta.pk if conta else None,
                "bandeira": item.bandeira,
                "parcelas": item.numero_parcelas,
                "valor_bruto": item.valor_bruto_recebido,
                "valor_taxa": item.valor_taxa,
                "taxa_percentual": item.taxa_percentual_aplicada,
                "taxa_fixa": item.taxa_fixa_aplicada,
                "valor_liquido": item.valor_entrada_liquida,
                "origem_codigo": "venda",
                "registro_id": item.pk,
                "referencia_url": "",
                "atrasado": False,
                "renegociado": False,
            })
        recebimentos = ContaReceber.objects.filter(
            filial=self.filial,
            status__in=("aberto", "pago_parcial", "vencido", "negociado"),
            data_vencimento__lte=data_fim,
            valor_saldo__gt=0,
        ).select_related("cliente", "forma_pagamento", "conta_bancaria", "plano_contas")
        for item in recebimentos:
            forma = item.forma_pagamento
            conta = item.conta_bancaria or (forma.conta_bancaria_padrao if forma else None)
            prazo = forma.prazo_compensacao_dias_uteis if forma else 0
            data_prevista = adicionar_dias_uteis_bancarios(item.data_vencimento, prazo, self.filial)
            atrasado = item.data_vencimento < hoje
            if atrasado and data_inicio <= hoje <= data_fim:
                data_prevista = item.data_vencimento
            elif not data_inicio <= data_prevista <= data_fim:
                continue
            itens.append({
                "data": data_prevista,
                "descricao": f"Conta a receber - {item.cliente}",
                "classificacao": item.plano_contas.descricao if item.plano_contas_id else "Conta a receber",
                "forma": forma.descricao if forma else "Sem forma vinculada",
                "conta": conta.descricao if conta else "Conta nao definida",
                "conta_id": conta.pk if conta else None,
                "bandeira": "",
                "parcelas": item.total_parcelas,
                "valor_bruto": item.valor_saldo,
                # A forma vinculada ao título é apenas uma previsão enquanto não
                # houver baixa. A taxa pertence ao recebimento efetivo e pode até
                # mudar no momento da baixa; portanto, o saldo aberto deve ser
                # projetado integralmente.
                "valor_taxa": ZERO,
                "taxa_percentual": ZERO,
                "taxa_fixa": ZERO,
                "valor_liquido": item.valor_saldo,
                "origem_codigo": "receber",
                "registro_id": item.pk,
                "referencia_url": reverse("financeiro:receber_detail", args=[item.pk]),
                "atrasado": atrasado,
                "renegociado": item.status == StatusContaReceber.NEGOCIADO,
            })
        itens.sort(key=lambda item: (0 if item.get("atrasado") else 1, item["data"], item["descricao"].casefold()))
        return itens
