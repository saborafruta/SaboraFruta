"""
Gera o contas a receber a partir do pedido de produção.

A regra que sustenta o resto do arquivo é uma só: **a soma das contas
geradas é exatamente o valor total do pedido**. Se a tela mostra R$ 12.400
e o financeiro recebe R$ 12.380, alguém vai passar uma tarde procurando os
R$ 20 — então o arredondamento das parcelas é resolvido aqui dentro, com a
última parcela absorvendo a diferença.

A entrada informada no pedido já é dinheiro recebido: vira uma conta
recebida e baixada na data do pedido, pela forma/conta bancaria escolhida.
Só o saldo restante nasce como contas em aberto.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.core.tenant_context import tenant_atomic
from apps.core.services.exceptions import DomainError
from apps.financeiro.constants.enums import StatusContaReceber
from apps.financeiro.models.receber_pagar import ContaReceber
from apps.financeiro.services.receber_service import ContaReceberService
from apps.financeiro.services.categorias_receita import categoria_vendas_produtos

CENTAVO = Decimal('0.01')
PRAZO_LIMITE_SALDO_DIAS = 30


@dataclass(frozen=True)
class Parcela:
    """Uma linha do plano de pagamento, antes de virar registro no banco."""
    numero: int
    valor: Decimal
    vencimento: date
    rotulo: str


@dataclass(frozen=True)
class ResultadoRecebimentoPedido:
    valor_recebido: Decimal
    valor_taxa: Decimal
    saldo_restante: Decimal
    pagamentos_criados: int


class FinanceiroPedidoService:

    # Grava no `documento_tipo` da conta. É por ele que o pedido reencontra
    # o que gerou -- e é essa consulta, não um booleano no pedido, que
    # impede gerar duas vezes.
    DOCUMENTO_TIPO = 'pedido_moda'

    # ── Consulta ─────────────────────────────────────────────────────────

    @classmethod
    def contas_do_pedido(cls, pedido):
        """Contas geradas por este pedido que ainda valem (canceladas fora)."""
        return (
            ContaReceber.objects
            .filter(documento_tipo=cls.DOCUMENTO_TIPO, documento_id=pedido.pk)
            .exclude(status=StatusContaReceber.CANCELADO)
            .order_by('parcela')
        )

    @classmethod
    def sincronizar_previsao_entrega(cls, pedido):
        """Espelha a previsão comercial nos títulos já gerados da OP."""
        ContaReceber.objects.filter(
            documento_tipo=cls.DOCUMENTO_TIPO,
            documento_id=pedido.pk,
        ).update(
            status_entrega=(
                ContaReceber.StatusEntrega.PREVISTA
                if pedido.data_prevista_entrega else ContaReceber.StatusEntrega.SEM_PREVISAO
            ),
            data_entrega_prevista=pedido.data_prevista_entrega,
            previsao_entrega_complemento='',
        )

    @classmethod
    @tenant_atomic
    def sincronizar_valor_total(cls, pedido, usuario=None):
        """Faz os títulos válidos somarem exatamente o novo total da OP.

        O valor já recebido é um piso: um desconto nunca pode apagar dinheiro
        que já entrou. O saldo novo é repartido na mesma proporção dos saldos
        anteriores, preservando cliente, vencimento e quantidade de parcelas.
        """
        contas = list(
            ContaReceber.objects.select_for_update()
            .filter(documento_tipo=cls.DOCUMENTO_TIPO, documento_id=pedido.pk)
            .exclude(status=StatusContaReceber.CANCELADO)
            .order_by('parcela', 'pk')
        )
        if not contas:
            return []

        total_novo = pedido.valor_total.quantize(CENTAVO)
        total_recebido = sum((conta.valor_pago for conta in contas), Decimal('0'))
        if total_novo < total_recebido:
            raise DomainError(
                'O valor final não pode ficar abaixo do que já foi recebido '
                f'(R$ {total_recebido:.2f}).'
            )

        saldo_novo = total_novo - total_recebido
        saldo_anterior = sum((conta.valor_saldo for conta in contas), Decimal('0'))
        saldos = []
        distribuido = Decimal('0')
        for indice, conta in enumerate(contas):
            if indice == len(contas) - 1:
                parcela_saldo = saldo_novo - distribuido
            elif saldo_anterior > 0:
                parcela_saldo = (
                    saldo_novo * conta.valor_saldo / saldo_anterior
                ).quantize(CENTAVO, rounding=ROUND_DOWN)
            else:
                parcela_saldo = Decimal('0')
            distribuido += parcela_saldo
            saldos.append(parcela_saldo)

        # Quando tudo já estava pago e o valor aumentou, a diferença fica na
        # última parcela em vez de criar um título solto sem referência.
        if saldo_anterior <= 0 and saldos:
            saldos[-1] = saldo_novo

        atualizadas = []
        for conta, saldo in zip(contas, saldos):
            alvo_final = conta.valor_pago + saldo
            valor_original = (
                alvo_final - conta.valor_juros - conta.valor_multa
                + conta.valor_desconto
            )
            if valor_original < 0:
                raise DomainError(
                    'Um título possui juros ou descontos próprios que impedem '
                    'o ajuste automático. Edite esse título no contas a receber.'
                )
            atualizadas.append(ContaReceberService.editar(
                conta=conta,
                dados={'valor_original': valor_original.quantize(CENTAVO)},
                usuario=usuario,
            ))
        return atualizadas

    @staticmethod
    def situacao_pagamento(valor_titulos=0, valor_recebido=0, valor_aberto=0):
        """Situação dos títulos válidos, sem descontar taxas do valor recebido."""
        if valor_titulos > 0 and valor_aberto <= 0:
            return {'chave': 'pago', 'rotulo': 'Pago'}
        if valor_recebido > 0:
            return {'chave': 'parcial', 'rotulo': 'Pagamento parcial'}
        return {'chave': 'pendente', 'rotulo': 'Pagamento pendente'}

    @classmethod
    def situacoes_dos_pedidos(cls, pedidos, *, filial):
        """Carrega as tags do quadro em uma consulta, sem uma busca por cartão."""
        resumos = (
            ContaReceber.objects.for_filial(filial)
            .filter(documento_tipo=cls.DOCUMENTO_TIPO,
                    documento_id__in=[pedido.pk for pedido in pedidos])
            .exclude(status=StatusContaReceber.CANCELADO)
            .order_by().values('documento_id')
            .annotate(valor_titulos=Sum('valor_final'),
                      valor_recebido=Sum('valor_pago'),
                      valor_aberto=Sum('valor_saldo'))
        )
        return {
            resumo['documento_id']: cls.situacao_pagamento(
                resumo['valor_titulos'], resumo['valor_recebido'],
                resumo['valor_aberto'],
            )
            for resumo in resumos
        }

    @classmethod
    def pedidos_com_saldo(cls, filial):
        """Lista OPs com valor comercial ainda não recebido.

        A OP entra na lista mesmo antes de ``gerar`` ser usado. Nesse caso o
        valor total comercial é a dívida e nenhum sinal digitado na OP é
        considerado dinheiro recebido até existir uma baixa real.
        """
        from apps.moda.models import PedidoProducao

        pedidos = list(
            PedidoProducao.objects.for_filial(filial)
            .exclude(status=PedidoProducao.Status.CANCELADO)
            .select_related('cliente')
            .prefetch_related('itens')
            .order_by('-data_pedido', '-numero')
        )
        recebidos = {
            linha['documento_id']: linha['valor_recebido'] or Decimal('0')
            for linha in (
                ContaReceber.objects.for_filial(filial)
                .filter(
                    documento_tipo=cls.DOCUMENTO_TIPO,
                    documento_id__in=[pedido.pk for pedido in pedidos],
                )
                .exclude(status=StatusContaReceber.CANCELADO)
                .order_by()
                .values('documento_id')
                .annotate(valor_recebido=Sum('valor_pago'))
            )
        }
        ids_com_financeiro = set(recebidos)
        pendentes = []
        for pedido in pedidos:
            total = pedido.valor_total.quantize(CENTAVO)
            recebido = recebidos.get(pedido.pk, Decimal('0')).quantize(CENTAVO)
            saldo = max(total - recebido, Decimal('0')).quantize(CENTAVO)
            if total <= 0 or saldo <= 0:
                continue
            pedido.valor_recebido_op = recebido
            pedido.valor_aberto_op = saldo
            pedido.tem_financeiro_op = pedido.pk in ids_com_financeiro
            pedido.pagamento_parcial_op = recebido > 0
            pendentes.append(pedido)
        return pendentes

    @classmethod
    @tenant_atomic
    def receber(
        cls, pedido, *, data_pagamento, valor_pago, forma_pagamento, usuario,
        conta_bancaria=None, observacao='', bandeira='', numero_parcelas=None,
        valor_taxa=None, valor_liquido=None,
    ) -> ResultadoRecebimentoPedido:
        """Recebe uma OP, criando seu título quando ele ainda não existe."""
        from apps.moda.models import PedidoProducao

        pedido = (
            PedidoProducao.objects.select_for_update()
            .select_related('cliente', 'filial')
            .prefetch_related('itens')
            .get(pk=pedido.pk, filial_id=pedido.filial_id)
        )
        if pedido.status == pedido.Status.CANCELADO:
            raise DomainError('Pedido cancelado não pode receber pagamento.')

        valor_pago = Decimal(valor_pago).quantize(CENTAVO)
        if valor_pago <= 0:
            raise DomainError('Informe um valor recebido maior que zero.')

        contas = list(
            ContaReceber.objects.select_for_update()
            .filter(documento_tipo=cls.DOCUMENTO_TIPO, documento_id=pedido.pk)
            .exclude(status=StatusContaReceber.CANCELADO)
            .order_by('data_vencimento', 'parcela', 'pk')
        )
        total_pedido = pedido.valor_total.quantize(CENTAVO)
        total_recebido = sum((conta.valor_pago for conta in contas), Decimal('0'))
        saldo_pedido = (total_pedido - total_recebido).quantize(CENTAVO)
        if saldo_pedido <= 0:
            raise DomainError('Esta OP já está integralmente paga.')
        if valor_pago > saldo_pedido:
            raise DomainError(
                f'O valor recebido não pode passar do saldo da OP de R$ {saldo_pedido:.2f}.'
            )

        if contas:
            total_titulos = sum((conta.valor_final for conta in contas), Decimal('0'))
            if total_titulos != total_pedido:
                cls.sincronizar_valor_total(pedido, usuario=usuario)
                contas = list(
                    ContaReceber.objects.select_for_update()
                    .filter(documento_tipo=cls.DOCUMENTO_TIPO, documento_id=pedido.pk)
                    .exclude(status=StatusContaReceber.CANCELADO)
                    .order_by('data_vencimento', 'parcela', 'pk')
                )
        else:
            contas = [cls._criar_titulo_integral(pedido, usuario)]

        if valor_taxa is None and valor_liquido is None:
            calculo = forma_pagamento.calcular_taxa_recebimento(
                valor_pago, numero_parcelas or 1, bandeira or '',
            )
            taxa_total = calculo['taxa'].quantize(CENTAVO)
            liquido_total = calculo['liquido'].quantize(CENTAVO)
        else:
            taxa_total = (
                Decimal(valor_taxa).quantize(CENTAVO)
                if valor_taxa is not None
                else (valor_pago - Decimal(valor_liquido)).quantize(CENTAVO)
            )
            liquido_total = (
                Decimal(valor_liquido).quantize(CENTAVO)
                if valor_liquido is not None else valor_pago - taxa_total
            )
        if (
            taxa_total < 0 or taxa_total > valor_pago or liquido_total < 0
            or abs((valor_pago - taxa_total) - liquido_total) > CENTAVO
        ):
            raise DomainError('A taxa e o valor final do recebimento são inválidos.')
        restante = valor_pago
        taxa_distribuida = Decimal('0')
        pagamentos = 0
        abertas = [conta for conta in contas if conta.valor_saldo > 0]
        for indice, conta in enumerate(abertas):
            if restante <= 0:
                break
            parte = min(restante, conta.valor_saldo).quantize(CENTAVO)
            ultima = parte == restante or indice == len(abertas) - 1
            if ultima:
                taxa_parte = taxa_total - taxa_distribuida
            else:
                taxa_parte = (taxa_total * parte / valor_pago).quantize(
                    CENTAVO, rounding=ROUND_DOWN,
                )
            taxa_distribuida += taxa_parte
            ContaReceberService.registrar_baixa(
                conta, data_pagamento, parte, forma_pagamento, usuario,
                conta_bancaria=conta_bancaria,
                observacao=(observacao or f'Recebimento da OP #{pedido.numero:06d}'),
                bandeira=bandeira,
                numero_parcelas=numero_parcelas,
                valor_taxa=taxa_parte,
                valor_liquido=parte - taxa_parte,
            )
            restante -= parte
            pagamentos += 1

        if restante > 0:
            raise DomainError('Não foi possível distribuir o recebimento entre os títulos da OP.')

        if not pedido.financeiro_gerado_em:
            pedido.financeiro_gerado_em = timezone.now()
            pedido.save(update_fields=['financeiro_gerado_em', 'updated_at'])
        return ResultadoRecebimentoPedido(
            valor_recebido=valor_pago,
            valor_taxa=taxa_total,
            saldo_restante=(saldo_pedido - valor_pago).quantize(CENTAVO),
            pagamentos_criados=pagamentos,
        )

    @classmethod
    def _criar_titulo_integral(cls, pedido, usuario):
        categoria = categoria_vendas_produtos(pedido.filial)
        vencimento = cls._data_base_saldo(pedido)
        conta = ContaReceber(
            filial=pedido.filial,
            cliente=pedido.cliente,
            documento_tipo=cls.DOCUMENTO_TIPO,
            documento_id=pedido.pk,
            documento_numero=f'OP #{pedido.numero:06d} · {pedido.cliente}',
            parcela=1,
            total_parcelas=1,
            valor_original=pedido.valor_total.quantize(CENTAVO),
            valor_final=pedido.valor_total.quantize(CENTAVO),
            valor_saldo=pedido.valor_total.quantize(CENTAVO),
            data_emissao=pedido.data_pedido,
            data_vencimento=vencimento,
            status_entrega=(
                ContaReceber.StatusEntrega.PREVISTA
                if pedido.data_prevista_entrega else ContaReceber.StatusEntrega.SEM_PREVISAO
            ),
            data_entrega_prevista=pedido.data_prevista_entrega,
            forma_pagamento=pedido.forma_pagamento,
            plano_contas=categoria,
            conta_contabil=categoria.conta_contabil if categoria else None,
            status=StatusContaReceber.ABERTO,
            observacao=f'Pedido de produção #{pedido.numero:06d} — cobrança criada no recebimento',
            usuario=usuario,
        )
        conta.save()
        return conta

    # ── Plano de pagamento ───────────────────────────────────────────────

    @classmethod
    def planejar(cls, pedido, *, vencimento_saldo=None, parcelas_saldo=None) -> list[Parcela]:
        """
        Monta o plano sem gravar nada -- é o que a tela mostra na prévia e
        o que o teste consegue conferir sem banco.

        A entrada, quando existe, é a parcela 1 e já será baixada na geração.
        O saldo parte da previsão de entrega, limitado a 30 dias do pedido.
        A condição de pagamento só divide esse saldo em parcelas.
        """
        parcelas: list[Parcela] = []
        entrada = pedido.entrada or Decimal('0')
        saldo = pedido.saldo

        if entrada > 0:
            parcelas.append(Parcela(
                numero=1,
                valor=entrada.quantize(CENTAVO),
                vencimento=pedido.data_pedido,
                rotulo='Entrada recebida',
            ))

        if saldo > 0:
            parcelas.extend(cls._parcelar_saldo(
                pedido, saldo, inicio=len(parcelas),
                vencimento=vencimento_saldo, quantidade=parcelas_saldo,
            ))

        return parcelas

    @classmethod
    def _parcelar_saldo(
        cls, pedido, saldo: Decimal, inicio: int, *, vencimento=None, quantidade=None,
    ) -> list[Parcela]:
        cond = pedido.condicao_pagamento
        quantidade_informada = quantidade is not None
        quantidade = max(1, int(quantidade or (cond.numero_parcelas if cond else 1)))
        intervalo = cond.intervalo_dias if cond else 30
        # Ao alterar manualmente a quantidade no modal, uma condição "à
        # vista" (intervalo zero) não pode fazer todas vencerem no mesmo dia.
        if quantidade_informada and quantidade > 1 and intervalo <= 0:
            intervalo = 30
        primeira = cond.dias_primeira_parcela if cond else 0

        base_data = vencimento or cls._data_base_saldo(pedido)
        if vencimento:
            primeira = 0

        # Arredondamento para baixo em todas menos a última: assim a soma
        # nunca ultrapassa o saldo, e a diferença de centavos fica visível
        # numa parcela só, em vez de espalhada.
        valor_base = (saldo / quantidade).quantize(CENTAVO, rounding=ROUND_DOWN)
        distribuido = valor_base * (quantidade - 1)

        parcelas = []
        for i in range(quantidade):
            ultima = i == quantidade - 1
            parcelas.append(Parcela(
                numero=inicio + i + 1,
                valor=(saldo - distribuido) if ultima else valor_base,
                vencimento=base_data + timedelta(days=primeira + i * intervalo),
                rotulo=f'Parcela {i + 1}/{quantidade}',
            ))
        return parcelas

    @classmethod
    def _data_base_saldo(cls, pedido) -> date:
        """
        O saldo deve acompanhar a entrega esperada, mas não pode ficar sem
        limite. Se a entrega passar de 30 dias, o financeiro cobra no limite.
        """
        limite = pedido.data_pedido + timedelta(days=PRAZO_LIMITE_SALDO_DIAS)
        prevista = pedido.data_prevista_entrega
        if not prevista:
            return limite
        return min(prevista, limite)

    # ── Geração ──────────────────────────────────────────────────────────

    @classmethod
    @tenant_atomic
    def gerar(
        cls, pedido, usuario=None, *, vencimento_saldo=None, parcelas_saldo=None,
        pagadores_entrada=None, devedores_saldo=None,
    ) -> list[ContaReceber]:
        cls._validar(pedido)

        plano = cls.planejar(
            pedido, vencimento_saldo=vencimento_saldo,
            parcelas_saldo=parcelas_saldo,
        )
        if not plano:
            raise DomainError('Não há valor a receber neste pedido.')

        pagadores_entrada = list(pagadores_entrada or [pedido.cliente])
        devedores_saldo = list(devedores_saldo or [pedido.cliente])
        expandidas = []
        for parcela in plano:
            entrada = cls._eh_entrada(pedido, parcela)
            responsaveis = pagadores_entrada if entrada else devedores_saldo
            valores = cls._dividir_valor(parcela.valor, len(responsaveis))
            expandidas.extend(
                (parcela, cliente, valor, entrada)
                for cliente, valor in zip(responsaveis, valores)
            )

        total = len(expandidas)
        contas = []
        categoria = categoria_vendas_produtos(pedido.filial)
        for numero, (p, cliente, valor, entrada) in enumerate(expandidas, start=1):
            conta = ContaReceber(
                filial=pedido.filial,
                cliente=cliente,
                documento_tipo=cls.DOCUMENTO_TIPO,
                documento_id=pedido.pk,
                documento_numero=f"OP #{pedido.numero:06d} · {cliente}",
                parcela=numero,
                total_parcelas=total,
                valor_original=valor,
                valor_final=valor,
                valor_saldo=valor,
                data_emissao=pedido.data_pedido,
                data_vencimento=p.vencimento,
                status_entrega=(
                    ContaReceber.StatusEntrega.PREVISTA
                    if pedido.data_prevista_entrega else ContaReceber.StatusEntrega.SEM_PREVISAO
                ),
                data_entrega_prevista=pedido.data_prevista_entrega,
                forma_pagamento=pedido.forma_pagamento,
                plano_contas=categoria,
                conta_contabil=categoria.conta_contabil if categoria else None,
                conta_bancaria=(
                    pedido.conta_bancaria_entrada
                    or (
                        pedido.forma_pagamento.conta_bancaria_padrao
                        if pedido.forma_pagamento_id else None
                    )
                ),
                status=StatusContaReceber.ABERTO,
                observacao=(
                    f'Pedido de produção #{pedido.numero:06d} — {p.rotulo} — '
                    f'Responsável: {cliente.nome_display}'
                ),
                usuario=usuario,
            )
            conta.save()
            if entrada:
                ContaReceberService.registrar_baixa(
                    conta,
                    pedido.data_pedido,
                    valor,
                    pedido.forma_pagamento,
                    usuario,
                    conta_bancaria=cls._conta_entrada(pedido),
                    observacao=f'Entrada do pedido #{pedido.numero:06d}',
                )
                conta.refresh_from_db()
            contas.append(conta)

        pedido.financeiro_gerado_em = timezone.now()
        pedido.save(update_fields=['financeiro_gerado_em', 'updated_at'])
        return contas

    @staticmethod
    def _dividir_valor(valor: Decimal, quantidade: int) -> list[Decimal]:
        """Divide em centavos e deixa a diferença apenas na última pessoa."""
        if quantidade < 1:
            raise DomainError('Informe ao menos um responsável financeiro.')
        base = (valor / quantidade).quantize(CENTAVO, rounding=ROUND_DOWN)
        valores = [base] * (quantidade - 1)
        valores.append(valor - sum(valores, Decimal('0')))
        return valores

    @classmethod
    def _eh_entrada(cls, pedido, parcela: Parcela) -> bool:
        return (pedido.entrada or Decimal('0')) > 0 and parcela.numero == 1

    @classmethod
    def _conta_entrada(cls, pedido):
        return pedido.conta_bancaria_entrada or pedido.forma_pagamento.conta_bancaria_padrao

    @classmethod
    def _validar(cls, pedido) -> None:
        if pedido.status == pedido.Status.CANCELADO:
            raise DomainError('Pedido cancelado não gera financeiro.')

        if cls.contas_do_pedido(pedido).exists():
            raise DomainError(
                'Este pedido já tem financeiro gerado. Cancele o financeiro '
                'atual antes de gerar de novo.'
            )

        if pedido.valor_total <= 0:
            raise DomainError(
                'O pedido está sem valor. Preencha o valor unitário dos '
                'produtos antes de gerar o financeiro.'
            )

        if (pedido.entrada or Decimal('0')) > 0:
            if not pedido.forma_pagamento_id:
                raise DomainError(
                    'Informe a forma de pagamento da entrada antes de gerar o financeiro.'
                )
            if not cls._conta_entrada(pedido):
                raise DomainError(
                    'Informe a conta bancária da entrada ou configure uma conta padrão na forma de pagamento.'
                )

    # ── Cancelamento ─────────────────────────────────────────────────────

    @classmethod
    @tenant_atomic
    def cancelar(cls, pedido, usuario, motivo: str = '') -> int:
        """
        Desfaz o financeiro do pedido para poder gerar de novo.

        Existe porque a trava de "já gerou" deixaria o usuário preso: quem
        gerasse com o valor errado não teria como corrigir pela tela.

        Conta com qualquer recebimento não é cancelada -- o dinheiro já
        entrou, e apagar isso do financeiro seria pior do que o erro que se
        quer corrigir. Nesse caso o ajuste é no próprio contas a receber.
        """
        contas = list(cls.contas_do_pedido(pedido))
        if not contas:
            raise DomainError('Este pedido não tem financeiro gerado.')

        pagas = [c for c in contas if c.valor_pago > 0]
        if pagas:
            numeros = ', '.join(str(c.parcela) for c in pagas)
            raise DomainError(
                f'Não dá para cancelar: a(s) parcela(s) {numeros} já tem '
                f'recebimento lançado. Ajuste direto no contas a receber.'
            )

        # Import local: o serviço de contas a receber importa modelos do
        # financeiro, e o financeiro não precisa conhecer o moda. Deixar no
        # topo criaria um acoplamento que só existe nesta função.
        from apps.financeiro.services.receber_service import ContaReceberService

        texto = motivo or f'Financeiro do pedido #{pedido.numero:06d} refeito.'
        for conta in contas:
            ContaReceberService.cancelar(conta, texto, usuario)

        pedido.financeiro_gerado_em = None
        pedido.save(update_fields=['financeiro_gerado_em', 'updated_at'])
        return len(contas)
