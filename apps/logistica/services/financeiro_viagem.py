"""
O dinheiro da venda feita na rua.

A VENDA QUE NÃO VIRAVA COBRANÇA

A mercadoria saía, a nota saía, o saldo da viagem baixava — e o valor ficava
só no papel da venda. Ninguém devia nada a ninguém no financeiro: no dia
seguinte, quem cobra abria contas a receber e não via o cliente que levou
trinta caixas no caminhão. Uma venda a prazo sem título é uma venda que a
empresa esquece de cobrar, e é assim que se perde dinheiro sem ninguém errar
nada visível.

A FORMA DE PAGAMENTO DECIDE, E NÃO ESTE SERVIÇO

`FormaPagamento.gera_parcelas` já é a chave que o resto do ERP usa para
separar "recebi agora" de "vou receber depois". Repetir aqui uma lista de
formas que geram título criaria uma segunda regra que divergiria da primeira
no dia em que alguém cadastrasse uma forma nova.

DINHEIRO NA ENTREGA NÃO VIRA TÍTULO, e isso é decisão e não esquecimento:
abrir uma conta a receber já quitada encheria o contas a receber de linhas
que ninguém precisa cobrar e faria o total em aberto perder o significado. O
acerto do dinheiro que o motorista traz é a prestação de contas da viagem,
não uma cobrança ao cliente.

BONIFICAÇÃO NUNCA GERA TÍTULO. É a mesma razão pela qual ela não é registrada
como venda: ninguém paga por uma cortesia, e um título de bonificação
apareceria no faturamento como receita que não existe.

A CONDIÇÃO DE PAGAMENTO PARCELA

Número de parcelas, intervalo e dias da primeira vêm do cadastro da condição
— os mesmos campos que o resto do sistema usa. Sem condição escolhida, é uma
parcela no prazo da forma de pagamento, que é o que "a prazo" significa
quando ninguém disse mais nada.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.financeiro.constants.enums import StatusContaReceber
from apps.financeiro.models.receber_pagar import ContaReceber
from apps.logistica.models import VendaViagem

ZERO = Decimal('0')
CENTAVOS = Decimal('0.01')

# Como a conta a receber aponta de volta para a venda da rua.
ORIGEM = 'venda_viagem'

# Títulos que já viram dinheiro: cancelar a venda por cima deles esconderia
# recebimento em vez de desfazê-lo.
COM_DINHEIRO = (
    StatusContaReceber.PAGO,
    StatusContaReceber.PAGO_PARCIAL,
)


class FinanceiroViagemService:

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def titulos(venda):
        """As contas a receber desta venda."""
        return (
            ContaReceber.objects
            .filter(documento_tipo=ORIGEM, documento_id=venda.pk)
            .order_by('parcela')
        )

    @classmethod
    def resumo(cls, venda) -> dict:
        """
        O que a tela precisa dizer sobre o dinheiro desta venda.

        SEM TÍTULO NÃO É ERRO, mas também não é silêncio: a tela diz se foi
        recebido na entrega ou se falta cobrança, porque as duas ausências
        são parecidas na tela e completamente diferentes no caixa.
        """
        titulos = list(cls.titulos(venda))
        return {
            'titulos': titulos,
            'quantidade': len(titulos),
            'valor': sum((t.valor_final or ZERO for t in titulos), ZERO),
            'aberto': sum(
                (
                    t.valor_saldo or ZERO for t in titulos
                    if t.status != StatusContaReceber.CANCELADO
                ),
                ZERO,
            ),
            'gera_titulo': cls._gera_titulo(venda),
            'a_vista': (
                venda.tipo == VendaViagem.Tipo.VENDA
                and not cls._gera_titulo(venda)
            ),
        }

    # ── Escrita ──────────────────────────────────────────────────────────

    @classmethod
    def gerar_titulos(cls, venda, usuario=None) -> list:
        """
        Abre as contas a receber da venda, quando ela é a prazo.

        CHAMADO PELO REGISTRO DA VENDA, e não por uma rotina noturna: título
        que nasce depois é título que alguém pode esquecer de criar, e a
        diferença aparece como cliente sem cobrança.

        REPETIR NÃO DUPLICA. A venda pode ser salva de novo — pela tela, por
        importação, por um item a mais — e cada passagem aqui não pode virar
        uma segunda cobrança do mesmo valor.
        """
        if not cls._gera_titulo(venda):
            return []
        if cls.titulos(venda).exclude(status=StatusContaReceber.CANCELADO).exists():
            return []
        if not venda.cliente_id:
            # SEM CLIENTE NAO HA' A QUEM COBRAR. A venda na rua aceita nome
            # solto -- e' assim que ela funciona --, mas o titulo precisa de
            # cadastro, e inventar um cliente aqui seria pior do que nao
            # gerar.
            return []

        valor = (venda.valor_total or ZERO).quantize(CENTAVOS)
        if valor <= ZERO:
            return []

        emissao = timezone.localtime(venda.data).date() if venda.data else timezone.localdate()
        titulos = []
        for parcela, (vencimento, parcela_valor) in enumerate(
            cls._parcelas(venda, emissao, valor), start=1,
        ):
            titulos.append(ContaReceber.objects.create(
                filial=venda.viagem.filial,
                cliente_id=venda.cliente_id,
                documento_tipo=ORIGEM,
                documento_id=venda.pk,
                documento_numero=str(venda.numero),
                parcela=parcela,
                total_parcelas=cls._numero_de_parcelas(venda),
                valor_original=parcela_valor,
                valor_final=parcela_valor,
                valor_saldo=parcela_valor,
                data_emissao=emissao,
                data_vencimento=vencimento,
                forma_pagamento=venda.forma_pagamento,
                status=StatusContaReceber.ABERTO,
                observacao=(
                    f'Venda {venda.numero} na viagem '
                    f'#{venda.viagem.numero:06d} — {venda.cliente_nome}.'
                ),
                usuario=usuario or venda.vendedor,
            ))
        return titulos

    @classmethod
    def ajustar_titulos(cls, venda) -> list:
        """
        A venda ganhou mais um item — a cobrança acompanha o novo total.

        DEIXAR O TÍTULO PARA TRÁS SERIA PIOR DO QUE NÃO TER TÍTULO: o cliente
        levaria mercadoria a mais e o financeiro cobraria o valor antigo,
        sem que nada apontasse a diferença.

        COM DINHEIRO JÁ RECEBIDO, ESTE SERVIÇO PARA. Mudar o valor de uma
        parcela que já foi paga em parte é decisão de quem responde pelo
        financeiro, e não efeito colateral de um item digitado na rua.
        """
        titulos = list(
            cls.titulos(venda).exclude(status=StatusContaReceber.CANCELADO)
        )
        if not titulos:
            return cls.gerar_titulos(venda)
        if any(t.status in COM_DINHEIRO or (t.valor_pago or ZERO) > ZERO
               for t in titulos):
            raise DadosInvalidosError(
                'Esta venda já tem recebimento lançado no contas a receber. '
                'Ajuste o título antes de incluir mais itens.'
            )

        valor = (venda.valor_total or ZERO).quantize(CENTAVOS)
        emissao = titulos[0].data_emissao
        novas = cls._parcelas(venda, emissao, valor)
        for titulo, (vencimento, parcela_valor) in zip(titulos, novas):
            titulo.valor_original = parcela_valor
            titulo.valor_final = parcela_valor
            titulo.valor_saldo = parcela_valor
            titulo.data_vencimento = vencimento
            titulo.save(update_fields=[
                'valor_original', 'valor_final', 'valor_saldo',
                'data_vencimento', 'updated_at',
            ])
        return titulos

    @classmethod
    def cancelar_titulos(cls, venda) -> int:
        """
        Cancela as cobranças de uma venda cancelada.

        RECUSA QUANDO JÁ ENTROU DINHEIRO. Cancelar por cima de um título pago
        apagaria a cobrança e deixaria o recebimento órfão — o caminho certo
        é estornar o pagamento primeiro, com quem recebeu respondendo por
        isso.
        """
        titulos = cls.titulos(venda).exclude(status=StatusContaReceber.CANCELADO)
        if titulos.filter(status__in=COM_DINHEIRO).exists():
            raise DadosInvalidosError(
                'Esta venda já tem recebimento lançado no contas a receber. '
                'Estorne o pagamento antes de cancelar a venda.'
            )
        return titulos.update(
            status=StatusContaReceber.CANCELADO, valor_saldo=ZERO,
        )

    # ── As regras ────────────────────────────────────────────────────────

    @staticmethod
    def _gera_titulo(venda) -> bool:
        """
        Quem decide é o cadastro da forma de pagamento.

        Bonificação não paga nada, e venda sem forma escolhida é dinheiro na
        entrega — nenhuma das duas abre cobrança.
        """
        if venda.tipo != VendaViagem.Tipo.VENDA:
            return False
        forma = venda.forma_pagamento
        return bool(forma and forma.gera_parcelas)

    @staticmethod
    def _numero_de_parcelas(venda) -> int:
        condicao = venda.condicao_pagamento
        return max(1, int(getattr(condicao, 'numero_parcelas', 1) or 1))

    @classmethod
    def _parcelas(cls, venda, emissao, valor) -> list[tuple]:
        """
        As parcelas, com o resto de centavos na primeira.

        A SOBRA VAI PARA A PRIMEIRA, e não some: três parcelas de R$ 33,33
        somam R$ 99,99, e o centavo que falta é o que faz o cliente ficar
        devendo um centavo para sempre.
        """
        condicao = venda.condicao_pagamento
        quantidade = cls._numero_de_parcelas(venda)
        intervalo = int(getattr(condicao, 'intervalo_dias', 30) or 0)
        primeira = int(getattr(condicao, 'dias_primeira_parcela', 0) or 0)
        if condicao is None:
            primeira = int(
                getattr(venda.forma_pagamento, 'prazo_liquidacao_dias', 0) or 0
            )

        base = (valor / quantidade).quantize(CENTAVOS, rounding=ROUND_HALF_UP)
        parcelas = []
        for indice in range(quantidade):
            parcela = base if indice else valor - base * (quantidade - 1)
            parcelas.append((
                emissao + timedelta(days=primeira + intervalo * indice),
                parcela.quantize(CENTAVOS),
            ))
        return parcelas
