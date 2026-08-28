"""
Onde foi parar cada unidade que subiu no caminhão.

A CARGA TEM QUE FECHAR

    carga inicial = vendas já realizadas + venda na rua + bonificação
                    + retorno + o que ainda está no caminhão + baixas

Uma viagem de 360 unidades que vendeu 150 na fábrica, 180 na rua, bonificou
10 e devolveu 20 fecha em 360. Enquanto a conta não fecha, existe mercadoria
da empresa sem destino registrado — e o buraco não é de relatório, é de
mercadoria.

POR QUE ISTO NÃO É O `resumo` DA VIAGEM

O resumo responde "o que subiu no caminhão", separado por natureza: é a
conferência da doca. Este quadro responde a pergunta seguinte — "e onde
cada coisa foi parar?" —, que só existe depois que a viagem anda. As duas
leem os mesmos registros e não se substituem.

AS QUATRO COLUNAS SÃO DE NATUREZAS DIFERENTES, E POR ISSO SEPARADAS

  · VENDAS JÁ REALIZADAS saíram endereçadas: a mercadoria tem dono desde a
    doca, e o que se acompanha é a entrega;

  · VENDA FORA saiu sem comprador: o que importa é quanto ainda dá para
    vender, e esse número muda a cada parada;

  · BONIFICAÇÃO saiu de graça, e some no caminho sem que ninguém reclame;

  · RETORNO é o que voltou — e é ele que fecha a conta das outras três.

Somá-las num total só esconderia exatamente o que a operação precisa ver.

BONIFICAÇÃO QUE VOLTOU CONTA COMO RETORNO, E NÃO COMO BONIFICAÇÃO. Contá-la
nas duas colunas faria a soma passar da carga inicial — e a cortesia que
voltou não foi dada a ninguém.
"""
from __future__ import annotations

from decimal import Decimal

from apps.fiscal.models import NaturezaOperacao
from apps.logistica.models import EntregaBonificacao, ItemCarga

ZERO = Decimal('0')

E = NaturezaOperacao.Especie


class EstoqueViagemService:

    @staticmethod
    def _por_especie(viagem) -> dict:
        """Quanto saiu de cada natureza, pela carga."""
        totais = {}
        for item in ItemCarga.objects.filter(viagem=viagem).select_related('natureza'):
            especie = item.natureza.especie
            totais[especie] = totais.get(especie, ZERO) + (item.quantidade or ZERO)
        return totais

    @staticmethod
    def _bonificacao_da_carga_retornada(viagem) -> Decimal:
        """
        A cortesia que saiu endereçada e voltou.

        Ela conta como RETORNO, e não como bonificação: a mercadoria não foi
        dada a ninguém, e contá-la nas duas colunas faria a soma passar da
        carga inicial.
        """
        entregas = (
            EntregaBonificacao.objects
            .filter(
                item_carga__viagem=viagem,
                status=EntregaBonificacao.Status.RETORNADA,
            )
            .select_related('item_carga')
        )
        return sum(
            (e.item_carga.quantidade or ZERO for e in entregas), ZERO,
        )

    @classmethod
    def quadro(cls, viagem) -> dict:
        """
        As quatro colunas da operação, e a conta que precisa fechar.

        NADA É RECALCULADO AQUI: a carga responde pelo que saiu, o saldo da
        viagem responde pelo que aconteceu com a remessa, e o acompanhamento
        da bonificação responde pelo que voltou. Este quadro só os põe lado
        a lado.
        """
        por_especie = cls._por_especie(viagem)
        saldos = list(viagem.saldos.select_related('produto'))

        carga_inicial = sum(por_especie.values(), ZERO)

        vendas_realizadas = por_especie.get(E.VENDA, ZERO)
        remetido = por_especie.get(E.REMESSA_VENDA_FORA, ZERO)
        outras_remessas = por_especie.get(E.REMESSA_SIMPLES, ZERO)

        vendido_na_rua = sum(
            (s.quantidade_vendida or ZERO for s in saldos), ZERO,
        )
        bonificado_na_rua = sum(
            (s.quantidade_bonificada or ZERO for s in saldos), ZERO,
        )
        retornado_da_remessa = sum(
            (s.quantidade_retornada or ZERO for s in saldos), ZERO,
        )
        baixado = sum((s.quantidade_baixada or ZERO for s in saldos), ZERO)
        em_poder = sum((s.quantidade_em_poder for s in saldos), ZERO)

        bonificacao_carga = por_especie.get(E.BONIFICACAO, ZERO)
        bonificacao_voltou = cls._bonificacao_da_carga_retornada(viagem)
        bonificacao = bonificacao_carga - bonificacao_voltou + bonificado_na_rua
        retorno = retornado_da_remessa + bonificacao_voltou

        destinos = (
            vendas_realizadas + vendido_na_rua + bonificacao + retorno
            + em_poder + baixado + outras_remessas
        )

        return {
            'carga_inicial': carga_inicial,
            # ── As quatro colunas da especificação ───────────────────────
            'vendas_realizadas': vendas_realizadas,
            'venda_na_rua': vendido_na_rua,
            'bonificacao': bonificacao,
            'retorno': retorno,
            # ── O que a conta ainda precisa para fechar ──────────────────
            'em_poder': em_poder,
            'baixado': baixado,
            'outras_remessas': outras_remessas,
            # ── O detalhe de cada coluna ─────────────────────────────────
            'remetido': remetido,
            'disponivel_para_venda': em_poder,
            'bonificacao_da_carga': bonificacao_carga,
            'bonificacao_na_rua': bonificado_na_rua,
            'bonificacao_voltou': bonificacao_voltou,
            'retorno_da_remessa': retornado_da_remessa,
            # ── O fechamento ─────────────────────────────────────────────
            'destinos': destinos,
            'diferenca': (carga_inicial - destinos),
            'fecha': carga_inicial == destinos,
        }

    @classmethod
    def pendencias(cls, quadro: dict) -> list[str]:
        """
        O que impede a carga de fechar, em português.

        O NÚMERO SOZINHO NÃO DIZ O QUE FAZER. "Faltam 30" é o começo da
        pergunta; "30 ainda em poder da viagem — registre venda, bonificação,
        retorno ou baixa" é a resposta.
        """
        faltando = []
        if quadro['em_poder'] > ZERO:
            faltando.append(
                f'{quadro["em_poder"]} ainda em poder da viagem — registre '
                'venda, bonificação, retorno ou baixa.'
            )
        if not quadro['fecha'] and quadro['diferenca'] != quadro['em_poder']:
            # SOBRA OU FALTA QUE O SALDO NAO EXPLICA. E' o caso raro e o
            # unico que exige alguem olhar o registro: some quando a carga
            # foi alterada por fora do fluxo.
            faltando.append(
                f'A conta não fecha por {abs(quadro["diferenca"])} — a carga '
                'saiu com um número e os destinos somam outro.'
            )
        return faltando
