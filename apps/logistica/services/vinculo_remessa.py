"""
O vínculo entre a venda da rua e a NF-e de remessa.

A CADEIA QUE PRECISA SER LEGÍVEL DE UMA VEZ

    NF-e de remessa → viagem → produto (e lote) → venda → NF-e da venda

Cada elo já existia guardado em algum lugar: a remessa no documento fiscal
da viagem, o saldo por produto e lote em `SaldoCarga`, a venda em
`VendaViagem` e a nota dela no documento fiscal da venda. O que não existia
era a LEITURA — e um vínculo que só se monta abrindo quatro telas não serve
para o que ele existe: responder, com a fiscalização na mesa, de qual
remessa saiu a mercadoria de uma venda específica.

O QUE CADA LINHA RESPONDE

Chave, número, série e data são da REMESSA; produto, lote e quantidade
vendida são da VENDA; quantidade remetida e saldo são do LIVRO da viagem
(`SaldoCarga`), que é quem sabe quanto saiu e quanto ainda está no caminhão.
Nenhum desses números é recalculado aqui — são lidos de onde já são verdade.

O SALDO É O DE AGORA, E A NOTA DIZ ISSO. Ele muda a cada venda, bonificação
e retorno; congelá-lo na linha da venda daria um número que envelhece em
silêncio, e a pergunta "quanto ainda tem" passaria a ser respondida com o
saldo de ontem.

VÍNCULO VAZIO APARECE COMO VAZIO. Uma venda registrada antes de a remessa
ser emitida — o caminhão sai de madrugada, a nota sai às 8h — fica sem
vínculo até alguém emitir. Mostrar em branco é honesto; apontar para a nota
que existir na hora da consulta seria inventar um amparo que não existia
quando a mercadoria saiu.
"""
from __future__ import annotations

from decimal import Decimal

from apps.logistica.models import SaldoCarga, VendaViagem

ZERO = Decimal('0')


class VinculoRemessaService:

    @staticmethod
    def _saldos(viagem) -> dict:
        """O livro da viagem, indexado por produto e lote."""
        return {
            (saldo.produto_id, saldo.lote_id): saldo
            for saldo in SaldoCarga.objects.filter(viagem=viagem)
            .select_related('produto', 'lote')
        }

    @classmethod
    def linhas(cls, viagem, incluir_canceladas: bool = False) -> list[dict]:
        """
        Uma linha por item vendido, com a remessa de onde ele saiu.

        POR ITEM, e não por venda: é o item que tem produto e lote, e é por
        produto e lote que a remessa amparou a mercadoria. Uma venda com três
        produtos vira três linhas porque são três amparos diferentes.
        """
        saldos = cls._saldos(viagem)

        vendas = (
            VendaViagem.objects.filter(viagem=viagem)
            .select_related('documento_fiscal', 'cliente')
            .prefetch_related('itens__produto', 'itens__lote', 'itens__remessa')
            .order_by('numero')
        )
        if not incluir_canceladas:
            vendas = vendas.filter(status=VendaViagem.Status.REGISTRADA)

        linhas = []
        for venda in vendas:
            for item in venda.itens.all():
                saldo = saldos.get((item.produto_id, item.lote_id))
                remessa = item.remessa
                linhas.append({
                    'venda': venda,
                    'item': item,
                    'produto': item.produto,
                    'lote': item.lote,
                    'bonificacao': venda.bonificacao,
                    # ── A remessa ────────────────────────────────────────
                    'remessa': remessa,
                    'chave': getattr(remessa, 'chave', '') or '',
                    'numero': getattr(remessa, 'numero', None),
                    'serie': getattr(remessa, 'serie', None),
                    'data': getattr(remessa, 'data_emissao', None),
                    # SEM VINCULO NAO E' ERRO, e' pendencia: a venda saiu
                    # antes de a nota existir, e alguem precisa emiti-la.
                    'sem_vinculo': remessa is None,
                    # A REMESSA PODE NAO TER CHAVE ainda (nao transmitida) --
                    # e a chave e' o que a fiscalizacao procura primeiro.
                    'sem_chave': remessa is not None and not remessa.chave,
                    # ── As quantidades ───────────────────────────────────
                    'remetida': getattr(saldo, 'quantidade_remetida', None),
                    'vendida': item.quantidade or ZERO,
                    'saldo': getattr(saldo, 'quantidade_em_poder', None),
                    # ── A nota da venda ──────────────────────────────────
                    'nota_venda': venda.documento_fiscal,
                })
        return linhas

    @classmethod
    def resumo(cls, linhas: list[dict]) -> dict:
        """O que falta para a cadeia estar inteira."""
        return {
            'linhas': len(linhas),
            'sem_vinculo': sum(1 for l in linhas if l['sem_vinculo']),
            'sem_chave': sum(1 for l in linhas if l['sem_chave']),
            'sem_nota_venda': sum(1 for l in linhas if l['nota_venda'] is None),
        }

    @classmethod
    def por_remessa(cls, documento) -> list[dict]:
        """
        O caminho inverso: partindo da nota de remessa, o que ela amparou.

        É a pergunta da fiscalização quando ela chega pela NOTA, e não pela
        viagem — "esta remessa de 300 caixas virou o quê?".
        """
        itens = (
            documento.itens_venda_viagem
            .select_related(
                'produto', 'lote', 'venda', 'venda__viagem',
                'venda__documento_fiscal',
            )
            .order_by('venda__numero', 'pk')
        )
        return [
            {
                'venda': item.venda,
                'item': item,
                'produto': item.produto,
                'lote': item.lote,
                'vendida': item.quantidade or ZERO,
                'bonificacao': item.venda.bonificacao,
                'nota_venda': item.venda.documento_fiscal,
            }
            for item in itens
        ]
