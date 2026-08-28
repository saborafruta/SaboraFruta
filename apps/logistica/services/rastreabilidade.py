"""
Onde cada caixa esteve: estoque → remessa → viagem → venda/bonificação/retorno.

A PERGUNTA QUE ESTA TELA RESPONDE

    "Este produto saiu da minha prateleira. Onde ele foi parar?"

Ela é feita por três pessoas diferentes, e sempre no pior momento: o fiscal
que pede a justificativa da remessa; o cliente que reclama de um lote; o dono
que quer saber por que sobrou mercadoria no caminhão. Hoje cada um consegue
uma parte — o razão diz que baixou, a viagem diz o que vendeu, a nota diz o
que amparou — e ninguém consegue a linha inteira sem juntar três telas na
cabeça.

NADA É REGISTRADO AQUI, E ISSO É O PONTO

Este serviço não grava nada e não recalcula nada: ele lê o que os registros
já dizem e os põe em ordem. Um "histórico de rastreabilidade" gravado à parte
seria uma segunda verdade sobre a mesma caixa — e no dia em que divergisse do
razão, ninguém saberia qual acreditar. A linha do tempo é uma leitura, e por
isso ela nunca pode estar desatualizada.

A CADEIA É POR VIAGEM, E NÃO POR PRODUTO INTEIRO

Um produto que viajou cinco vezes tem cinco cadeias, cada uma com sua
remessa, suas vendas e seu retorno. Somar as cinco daria um total sem
significado nenhum — a pergunta "quanto voltou?" só existe dentro de uma
viagem. Por isso a tela lista cadeias, e o total de cada uma fecha sozinho.

VAZIO APARECE VAZIO

Viagem sem remessa emitida diz "sem NF-e", e não some da lista: a etapa que
falta é exatamente a que alguém precisa ver. Esconder a viagem porque a nota
ainda não saiu transformaria a pendência em invisibilidade.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from apps.estoque.models import MovimentacaoEstoque
from apps.fiscal.models import NaturezaOperacao
from apps.logistica.models import (
    ItemCarga, ItemVendaViagem, SaldoCarga, VendaViagem, Viagem,
)
from apps.logistica.services.remessa_nfe import RemessaVendaForaService
from apps.logistica.services.retorno_nfe import RetornoVendaForaService

ZERO = Decimal('0')

E = NaturezaOperacao.Especie

# As movimentações que a viagem escreve no razão.
SAIDA_DA_CARGA = 'viagem'
ENTRADA_DO_RETORNO = 'viagem_retorno'


class RastreabilidadeService:

    # ── Quem dá para rastrear ────────────────────────────────────────────

    @staticmethod
    def produtos_rastreaveis(filial):
        """
        Só os produtos que viajaram.

        UMA LISTA DE TODOS OS PRODUTOS SERIA INUTILIZÁVEL — e pior, mentiria
        por omissão: escolher um que nunca subiu no caminhão devolveria uma
        tela vazia que parece falha do sistema, e não ausência de viagem.
        """
        from apps.produtos.models import Produto

        return (
            Produto.objects
            .filter(itens_carga__viagem__filial=filial)
            .distinct()
            .order_by('descricao')
        )

    # ── A cadeia ─────────────────────────────────────────────────────────

    @classmethod
    def do_produto(cls, produto, filial, limite: int = 20) -> list[dict]:
        """
        Uma cadeia por viagem, da mais recente para a mais antiga.

        O LIMITE EXISTE PARA A TELA, e não para a verdade: quem precisa da
        viagem de dois anos atrás a abre pelo número. Trazer trezentas
        cadeias de uma vez faria a página demorar para responder a pergunta
        que quase sempre é sobre a última.
        """
        viagens = (
            Viagem.objects
            .filter(filial=filial, itens__produto=produto)
            .distinct()
            .order_by('-numero')[:limite]
        )
        return [cls.cadeia(viagem, produto) for viagem in viagens]

    @classmethod
    def cadeia(cls, viagem, produto) -> dict:
        """
        A linha inteira de um produto dentro de uma viagem.

        LIDA DE ONDE JÁ É VERDADE: o razão responde pela baixa e pela volta,
        a carga responde pelo que subiu, o saldo responde pelo que a remessa
        fez e as vendas respondem por quem levou.
        """
        itens = list(
            ItemCarga.objects
            .filter(viagem=viagem, produto=produto)
            .select_related('natureza', 'lote', 'cliente', 'documento_fiscal')
        )
        por_especie = {}
        for item in itens:
            por_especie[item.natureza.especie] = (
                por_especie.get(item.natureza.especie, ZERO)
                + (item.quantidade or ZERO)
            )

        saldos = list(
            SaldoCarga.objects.filter(viagem=viagem, produto=produto)
            .select_related('lote')
        )
        vendido = cls._soma_na_rua(viagem, produto, VendaViagem.Tipo.VENDA)
        bonificado_rua = cls._soma_na_rua(
            viagem, produto, VendaViagem.Tipo.BONIFICACAO,
        )

        cadeia = {
            'viagem': viagem,
            'produto': produto,
            'lotes': [s.lote for s in saldos if s.lote_id],
            'saiu_do_estoque': sum(
                (m.quantidade or ZERO for m in cls._saidas(viagem, produto)),
                ZERO,
            ),
            'carga': sum(por_especie.values(), ZERO),
            'vendas_realizadas': por_especie.get(E.VENDA, ZERO),
            'remetido': por_especie.get(E.REMESSA_VENDA_FORA, ZERO),
            'bonificacao_da_carga': por_especie.get(E.BONIFICACAO, ZERO),
            'outras_remessas': por_especie.get(E.REMESSA_SIMPLES, ZERO),
            'vendido': vendido,
            'bonificado_na_rua': bonificado_rua,
            'retornado': sum(
                (s.quantidade_retornada or ZERO for s in saldos), ZERO,
            ),
            'baixado': sum((s.quantidade_baixada or ZERO for s in saldos), ZERO),
            'em_poder': sum((s.quantidade_em_poder for s in saldos), ZERO),
            'remessa': RemessaVendaForaService.nota_da_viagem(viagem),
            'nota_retorno': RetornoVendaForaService.nota_da_viagem(viagem),
        }
        cadeia['eventos'] = cls._eventos(viagem, produto, cadeia)
        return cadeia

    # ── As etapas, na ordem em que aconteceram ───────────────────────────

    @classmethod
    def _eventos(cls, viagem, produto, cadeia) -> list[dict]:
        """
        A linha do tempo da especificação, etapa a etapa.

        A ORDEM É A DOS FATOS, e não a das tabelas: o estoque baixa, a
        remessa ampara, a viagem sai, e só então há venda, bonificação e
        retorno. Ler fora dessa ordem é o que faz alguém concluir que a
        mercadoria voltou antes de sair.

        ETAPA SEM REGISTRO NÃO É OMITIDA — é dita. É ela que explica por que
        a cadeia não fecha.
        """
        eventos = []

        for movimento in cls._saidas(viagem, produto):
            eventos.append({
                'etapa': 'estoque',
                'titulo': 'Baixa no estoque',
                'detalhe': (
                    f'{movimento.get_tipo_operacao_display()} · saldo de '
                    f'{movimento.quantidade_anterior} para '
                    f'{movimento.quantidade_posterior}'
                ),
                'quantidade': movimento.quantidade,
                'quando': movimento.data_movimentacao,
                'lote': movimento.lote,
                'documento': movimento.documento_fiscal,
            })

        if cadeia['remetido']:
            remessa = cadeia['remessa']
            eventos.append({
                'etapa': 'remessa',
                'titulo': (
                    f'NF-e de remessa nº {remessa.numero}'
                    if remessa is not None else 'Remessa sem NF-e emitida'
                ),
                'detalhe': (
                    f'série {remessa.serie} · {remessa.get_status_display()}'
                    if remessa is not None
                    else 'a mercadoria saiu sem documento de remessa'
                ),
                'quantidade': cadeia['remetido'],
                'quando': getattr(remessa, 'data_emissao', None),
                'lote': None,
                'documento': remessa,
            })

        eventos.append({
            'etapa': 'viagem',
            'titulo': f'Viagem nº {viagem.numero:06d}',
            'detalhe': (
                f'{viagem.motorista_nome or "sem motorista"} · '
                f'{viagem.veiculo_placa or "sem placa"} · '
                f'{viagem.get_status_display()}'
            ),
            'quantidade': cadeia['carga'],
            'quando': viagem.data_saida,
            'lote': None,
            'documento': None,
        })

        for item in cls._itens_na_rua(viagem, produto):
            venda = item.venda
            e_bonificacao = venda.tipo == VendaViagem.Tipo.BONIFICACAO
            eventos.append({
                'etapa': 'bonificacao' if e_bonificacao else 'venda',
                'titulo': (
                    f'{venda.get_tipo_display()} nº {venda.numero} — '
                    f'{venda.cliente_nome}'
                ),
                'detalhe': (
                    (venda.get_motivo_display() if venda.motivo else 'cortesia')
                    if e_bonificacao else
                    f'NF-e {venda.documento_fiscal.numero}'
                    if venda.documento_fiscal_id else 'sem NF-e emitida'
                ),
                'quantidade': item.quantidade,
                'quando': venda.data,
                'lote': item.lote,
                'documento': venda.documento_fiscal,
            })

        for item in cls._bonificacoes_da_carga(viagem, produto):
            eventos.append({
                'etapa': 'bonificacao',
                'titulo': f'Bonificação na carga — {item.cliente or "sem cliente"}',
                'detalhe': (
                    f'NF-e {item.documento_fiscal.numero}'
                    if item.documento_fiscal_id else 'sem NF-e emitida'
                ),
                'quantidade': item.quantidade,
                'quando': viagem.data_saida,
                'lote': item.lote,
                'documento': item.documento_fiscal,
            })

        for movimento in cls._entradas(viagem, produto):
            eventos.append({
                'etapa': 'retorno',
                'titulo': 'Retorno ao estoque',
                'detalhe': (
                    f'saldo de {movimento.quantidade_anterior} para '
                    f'{movimento.quantidade_posterior}'
                ),
                'quantidade': movimento.quantidade,
                'quando': movimento.data_movimentacao,
                'lote': movimento.lote,
                'documento': movimento.documento_fiscal,
            })

        if cadeia['nota_retorno'] is not None:
            nota = cadeia['nota_retorno']
            eventos.append({
                'etapa': 'retorno',
                'titulo': f'NF-e de retorno nº {nota.numero}',
                'detalhe': f'série {nota.serie} · {nota.get_status_display()}',
                'quantidade': cadeia['retornado'],
                'quando': nota.data_emissao,
                'lote': None,
                'documento': nota,
            })

        if cadeia['em_poder']:
            eventos.append({
                'etapa': 'pendente',
                'titulo': 'Ainda em poder da viagem',
                'detalhe': 'sem venda, bonificação, retorno ou baixa registrados',
                'quantidade': cadeia['em_poder'],
                'quando': None,
                'lote': None,
                'documento': None,
            })

        for evento in eventos:
            # DATA DE VIAGEM NAO TEM HORA, e a de um movimento tem. Formatar
            # as duas do mesmo jeito quebraria a pagina numa e inventaria
            # "00:00" na outra -- o evento diz o que ele sabe.
            evento['hora'] = isinstance(evento['quando'], datetime)
        return eventos

    # ── Leituras ─────────────────────────────────────────────────────────

    @staticmethod
    def _saidas(viagem, produto):
        return (
            MovimentacaoEstoque.objects
            .filter(
                documento_tipo=SAIDA_DA_CARGA, documento_id=viagem.pk,
                produto=produto,
            )
            .select_related('lote', 'usuario', 'documento_fiscal')
            .order_by('data_movimentacao')
        )

    @staticmethod
    def _entradas(viagem, produto):
        return (
            MovimentacaoEstoque.objects
            .filter(
                documento_tipo=ENTRADA_DO_RETORNO, documento_id=viagem.pk,
                produto=produto,
            )
            .select_related('lote', 'usuario', 'documento_fiscal')
            .order_by('data_movimentacao')
        )

    @staticmethod
    def _itens_na_rua(viagem, produto):
        return (
            ItemVendaViagem.objects
            .filter(
                venda__viagem=viagem, produto=produto,
                venda__status=VendaViagem.Status.REGISTRADA,
            )
            .select_related('venda', 'venda__documento_fiscal', 'lote')
            .order_by('venda__data', 'venda__numero')
        )

    @classmethod
    def _soma_na_rua(cls, viagem, produto, tipo) -> Decimal:
        return sum(
            (
                item.quantidade or ZERO
                for item in cls._itens_na_rua(viagem, produto)
                if item.venda.tipo == tipo
            ),
            ZERO,
        )

    @staticmethod
    def _bonificacoes_da_carga(viagem, produto):
        return (
            ItemCarga.objects
            .filter(
                viagem=viagem, produto=produto,
                natureza__especie=E.BONIFICACAO,
            )
            .select_related('cliente', 'lote', 'documento_fiscal')
            .order_by('id')
        )
