"""
O histórico das bonificações: o que saiu de graça, para quem e sob que nota.

AS DUAS BONIFICAÇÕES DA OPERAÇÃO, E POR QUE ELAS BAIXAM DIFERENTE

Uma bonificação pode sair por dois caminhos, e eles não são variações do
mesmo — são momentos diferentes da mercadoria:

  · NA CARGA. A cortesia já sai do estabelecimento endereçada a um cliente.
    O estoque da filial BAIXA quando a carga fecha, com movimento próprio no
    razão: 1.000 unidades viram 980 depois de 20 bonificadas;

  · NA RUA. A mercadoria saiu antes, amparada pela remessa, e a cortesia é
    decidida na frente do cliente. O estoque da filial JÁ BAIXOU na remessa —
    baixar de novo aqui tiraria a mesma caixa duas vezes. O que baixa é o
    saldo em poder da viagem.

Ler as duas na mesma tela sem dizer isso seria a pior das duas opções: quem
confere veria bonificações "sem baixa" e concluiria que o sistema perdeu
movimento. Por isso cada linha diz ONDE a baixa aconteceu.

O QUE CADA LINHA CARREGA

Produto, lote, quantidade, cliente, viagem, usuário, data/hora e a NF-e —
lidos de onde já são verdade. O razão responde pelas da carga; a entrega
registrada responde pelas da rua.

VÍNCULO VAZIO APARECE VAZIO. Bonificação cuja nota ainda não foi emitida
mostra "sem NF-e" — inventar um número ou esconder a linha seria pior do que
a pendência à vista.
"""
from __future__ import annotations

from decimal import Decimal

from apps.estoque.models import MovimentacaoEstoque
from apps.fiscal.models import NaturezaOperacao
from apps.logistica.models import ItemCarga, VendaViagem
from apps.logistica.services.entrega_bonificacao import (
    EntregaBonificacaoService,
)

ZERO = Decimal('0')

# Onde a baixa de estoque aconteceu, para a tela poder dizer.
NA_CARGA = 'carga'
NA_REMESSA = 'remessa'


class HistoricoBonificacaoService:

    @staticmethod
    def da_carga(viagem) -> list[dict]:
        """
        As bonificações que saíram do estabelecimento nesta viagem.

        Lidas pelo RAZÃO, e não pela carga: é o movimento que prova a baixa,
        e é ele que a conferência de estoque vai encontrar. A linha da carga
        diz o que se pretendia; o movimento diz o que aconteceu.
        """
        itens = {
            (i.produto_id, i.lote_id): i
            for i in ItemCarga.objects.filter(
                viagem=viagem,
                natureza__especie=NaturezaOperacao.Especie.BONIFICACAO,
            ).select_related('produto', 'lote', 'cliente', 'documento_fiscal')
        }
        if not itens:
            return []

        movimentos = (
            MovimentacaoEstoque.objects
            .filter(
                documento_tipo='viagem', documento_id=viagem.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.BONIFICACAO,
            )
            .select_related('produto', 'lote', 'cliente', 'usuario',
                            'documento_fiscal')
            .order_by('data_movimentacao')
        )

        linhas = []
        for movimento in movimentos:
            item = itens.get((movimento.produto_id, movimento.lote_id))
            acompanhamento = (
                EntregaBonificacaoService.para_item(item) if item else None
            )
            linhas.append({
                'acompanhamento': acompanhamento,
                'origem': NA_CARGA,
                'viagem': viagem,
                'produto': movimento.produto,
                'lote': movimento.lote,
                'quantidade': movimento.quantidade,
                'cliente': movimento.cliente or getattr(item, 'cliente', None),
                # AS DUAS ORIGENS DEVOLVEM AS MESMAS CHAVES: a tela le' as
                # duas no mesmo laco, e uma chave que so' existe de um lado
                # quebra a pagina quando a outra aparecer.
                'cliente_nome': str(
                    movimento.cliente or getattr(item, 'cliente', '') or ''
                ),
                'usuario': movimento.usuario,
                'quando': movimento.data_movimentacao,
                'nota': movimento.documento_fiscal or getattr(
                    item, 'documento_fiscal', None,
                ),
                'motivo': '',
                'saldo_apos': movimento.quantidade_posterior,
                'movimento': movimento,
            })
        return linhas

    @staticmethod
    def da_rua(viagem) -> list[dict]:
        """As bonificações entregues durante a rota, contra o saldo da carga."""
        entregas = (
            VendaViagem.objects
            .filter(
                viagem=viagem, tipo=VendaViagem.Tipo.BONIFICACAO,
                status=VendaViagem.Status.REGISTRADA,
            )
            .select_related('cliente', 'vendedor', 'documento_fiscal')
            .prefetch_related('itens__produto', 'itens__lote')
            .order_by('numero')
        )

        linhas = []
        for entrega in entregas:
            acompanhamento = EntregaBonificacaoService.para_entrega_da_rua(entrega)
            for item in entrega.itens.all():
                linhas.append({
                    'acompanhamento': acompanhamento,
                    'origem': NA_REMESSA,
                    'viagem': viagem,
                    'produto': item.produto,
                    'lote': item.lote,
                    'quantidade': item.quantidade or ZERO,
                    'cliente': entrega.cliente,
                    'cliente_nome': entrega.cliente_nome,
                    'usuario': entrega.vendedor,
                    'quando': entrega.data,
                    'nota': entrega.documento_fiscal,
                    'motivo': entrega.get_motivo_display() if entrega.motivo else '',
                    # A baixa de estoque desta cortesia aconteceu na remessa;
                    # o que ela consumiu foi o saldo da viagem.
                    'saldo_apos': None,
                    'entrega': entrega,
                })
        return linhas

    @classmethod
    def linhas(cls, viagem) -> list[dict]:
        """As duas juntas, em ordem de acontecimento."""
        todas = cls.da_carga(viagem) + cls.da_rua(viagem)
        return sorted(todas, key=lambda l: l['quando'] or l['viagem'].data_saida)

    @classmethod
    def resumo(cls, linhas: list[dict]) -> dict:
        return {
            'linhas': len(linhas),
            'quantidade': sum((l['quantidade'] or ZERO for l in linhas), ZERO),
            'sem_nota': sum(1 for l in linhas if l['nota'] is None),
            # A CORTESIA QUE NAO SE SABE SE CHEGOU. E' a pergunta que a
            # bonificacao nao faz sozinha: ninguem pagou, ninguem reclama.
            'sem_entrega': sum(
                1 for l in linhas
                if l['acompanhamento'] and l['acompanhamento'].aberta
            ),
            'sem_prova': sum(
                1 for l in linhas
                if l['acompanhamento'] and l['acompanhamento'].entregue
                and not l['acompanhamento'].tem_prova
            ),
            'da_carga': sum(1 for l in linhas if l['origem'] == NA_CARGA),
            'da_rua': sum(1 for l in linhas if l['origem'] == NA_REMESSA),
        }
