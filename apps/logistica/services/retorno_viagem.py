"""
O que deve voltar quando o caminhão chega.

A CONTA QUE O SISTEMA FAZ SOZINHO

    enviado para venda fora − vendido − bonificado − baixado
    = quantidade que deve retornar

300 saíram, 180 venderam: 120 têm de voltar. Ninguém digita esse número —
ele já está no saldo da viagem desde a primeira venda, e pedir para alguém
recalculá-lo na porta do caminhão é pedir para errar.

O SISTEMA CALCULA, A PESSOA CONFERE

O previsto é o que a conta diz; o retorno é o que a contagem física
encontra. Eles quase sempre coincidem — e é justamente por isso que a
diferença importa quando aparece: ela é quebra, furto ou erro de
apontamento, e some se o sistema aceitar o previsto como se fosse fato.

Por isso a conferência propõe o número e aceita menos. O que faltar NÃO
vira baixa automática: baixa é declaração de perda, com responsável, e um
sistema que a emite sozinho ensina a fábrica a não olhar.

SÓ O QUE SAIU SEM COMPRADOR VOLTA POR AQUI

Mercadoria vendida saiu endereçada e a devolução dela é outra operação, com
outro documento. Bonificação que não foi entregue volta pelo acompanhamento
da cortesia. Este serviço responde pela remessa — que é a única mercadoria
que saiu da empresa continuando dela.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.core.services.exceptions import DadosInvalidosError
from apps.logistica.models import Viagem
from apps.logistica.services.viagem import ViagemService

ZERO = Decimal('0')


class RetornoViagemService:

    # ── O que deve voltar ────────────────────────────────────────────────

    @staticmethod
    def previsto(viagem) -> list[dict]:
        """
        Produto a produto: o que saiu, o que teve destino e o que deve voltar.

        A LINHA MOSTRA A CONTA INTEIRA, e não só o resultado. Quem confere na
        doca precisa poder verificar de onde saiu o 120 — senão o número vira
        ordem, e ordem que não se confere ninguém contesta quando está errada.
        """
        linhas = []
        for saldo in viagem.saldos.select_related('produto', 'lote'):
            linhas.append({
                'saldo': saldo,
                'produto': saldo.produto,
                'lote': saldo.lote,
                'remetida': saldo.quantidade_remetida or ZERO,
                'vendida': saldo.quantidade_vendida or ZERO,
                'bonificada': saldo.quantidade_bonificada or ZERO,
                'retornada': saldo.quantidade_retornada or ZERO,
                'baixada': saldo.quantidade_baixada or ZERO,
                # É o mesmo `em_poder` do saldo: o que saiu e ainda não teve
                # destino. Recalculá-lo aqui daria uma segunda conta para a
                # mesma pergunta.
                'a_retornar': saldo.quantidade_em_poder,
            })
        return linhas

    @classmethod
    def resumo(cls, linhas: list[dict]) -> dict:
        return {
            'produtos': len(linhas),
            'remetida': sum((l['remetida'] for l in linhas), ZERO),
            'vendida': sum((l['vendida'] for l in linhas), ZERO),
            'bonificada': sum((l['bonificada'] for l in linhas), ZERO),
            'a_retornar': sum((l['a_retornar'] for l in linhas), ZERO),
            'pendente': any(l['a_retornar'] > ZERO for l in linhas),
        }

    # ── A conferência ────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def registrar(cls, viagem, quantidades: dict, usuario=None) -> dict:
        """
        Registra o retorno conferido, linha a linha.

        ACEITA MENOS DO QUE O PREVISTO, e devolve a diferença em vez de
        escondê-la: 120 previstos e 118 contados são duas caixas que
        precisam de explicação, não um arredondamento.

        NUNCA MAIS DO QUE O PREVISTO — o serviço de saldo já recusa, e é o
        mesmo limite que impede o saldo de fechar negativo.
        """
        linhas = {
            (l['saldo'].produto_id, l['saldo'].lote_id): l
            for l in cls.previsto(viagem)
        }

        registrados = ZERO
        divergencias = []
        for chave, informado in (quantidades or {}).items():
            linha = linhas.get(chave)
            if linha is None:
                continue
            quantidade = Decimal(str(informado or 0))
            if quantidade <= ZERO:
                continue

            ViagemService.registrar_retorno(
                viagem, linha['produto'], quantidade,
                lote=linha['lote'], usuario=usuario,
            )
            registrados += quantidade

            falta = linha['a_retornar'] - quantidade
            if falta > ZERO:
                divergencias.append({
                    'produto': linha['produto'],
                    'previsto': linha['a_retornar'],
                    'conferido': quantidade,
                    'diferenca': falta,
                })

        if registrados <= ZERO:
            raise DadosInvalidosError(
                'Nenhuma quantidade informada para retorno.'
            )
        return {'registrado': registrados, 'divergencias': divergencias}

    @classmethod
    def registrar_tudo(cls, viagem, usuario=None) -> dict:
        """
        O caso comum: o caminhão voltou e tudo que sobrou entrou.

        EXISTE PARA NÃO OBRIGAR A REDIGITAR o que o sistema já calculou. A
        conferência item a item continua ali para quando a contagem diverge
        — que é quando ela importa.
        """
        quantidades = {
            (l['saldo'].produto_id, l['saldo'].lote_id): l['a_retornar']
            for l in cls.previsto(viagem)
            if l['a_retornar'] > ZERO
        }
        if not quantidades:
            raise DadosInvalidosError(
                'Não há mercadoria em poder da viagem para retornar.'
            )
        return cls.registrar(viagem, quantidades, usuario=usuario)

    # ── O encerramento ───────────────────────────────────────────────────

    @classmethod
    def pode_encerrar(cls, viagem) -> list[str]:
        """
        O que falta para a viagem fechar — a mesma regra do encerramento.

        NÃO DUPLICA A DECISÃO: quem recusa encerrar é o `ViagemService`.
        Aqui só se lê a resposta dele para mostrar na tela do retorno, onde
        a pessoa está justamente resolvendo o que falta.
        """
        if viagem.status in (Viagem.Status.FINALIZADA, Viagem.Status.CANCELADA):
            return []
        return ViagemService.pendencias_de_encerramento(viagem)
