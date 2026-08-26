"""
Os indicadores da fábrica — cinco perguntas, uma tela.

NENHUM NÚMERO NOVO NASCE AQUI. Tudo já foi registrado por quem fez o
trabalho: a ordem sabe o que produziu, o apontamento sabe onde a fruta se
perdeu, o lote sabe quando vence, a análise sabe se aprovou. O painel só
junta — e é por isso que ele pode estar certo. Um dashboard que guarda os
próprios totais é o primeiro lugar onde o sistema passa a discordar de si
mesmo, e ninguém descobre até alguém conferir na mão.

O QUE ELE NÃO FAZ: inventar número onde não há medição. Rendimento sem
ordem encerrada é `None`, não zero; giro sem consumo é `None`, não zero;
capacidade sem cadastro é `None`. Zero é uma afirmação — "não rendeu nada",
"não girou" — e é diferente de "ninguém mediu". Confundir os dois é como um
painel perde a confiança de quem olha: basta um número obviamente errado.

TODO INDICADOR TEM UMA JANELA, e ela aparece na tela. "Produção: 12.400 kg"
não quer dizer nada sem "no mês"; e comparar o mês corrente (que está pela
metade) com o mês passado inteiro é o erro que faz a fábrica achar que caiu
pela metade todo dia 15.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.estoque.models import Estoque, LoteProduto, MovimentacaoEstoque
from apps.polpa.models import (
    ApontamentoEtapa, FichaProduto, OrdemPolpa, Recurso,
)
from apps.polpa.services.armazenagem import ArmazenagemService

ZERO = Decimal('0')
CEM = Decimal('100')

# Quantos dias sem sair para um lote ser considerado parado. Um mês é o
# tempo em que a fábrica ainda consegue vender com desconto; depois disso a
# conversa é outra.
DIAS_PARADO = 30


class IndicadoresService:

    # ══════════════════════════════════════════════════════════════════
    # PRODUÇÃO
    # ══════════════════════════════════════════════════════════════════

    @classmethod
    def producao(cls, filial, hoje=None) -> dict:
        """
        O que a fábrica produziu — hoje, na semana e no mês.

        SÓ ORDEM PRODUZIDA CONTA. Ordem aberta é intenção, e somá-la faria o
        painel prometer produto que ainda não existe.
        """
        hoje = hoje or timezone.localdate()
        semana = hoje - timedelta(days=hoje.weekday())
        mes = hoje.replace(day=1)

        produzidas = list(
            OrdemPolpa.objects.for_filial(filial)
            .filter(situacao=OrdemPolpa.Situacao.PRODUZIDA)
            .select_related('ordem', 'ordem__produto_acabado')
        )

        def no_periodo(inicio):
            dentro = []
            for op in produzidas:
                fim = op.ordem.data_fim_real
                if fim and timezone.localtime(fim).date() >= inicio:
                    dentro.append(op)
            return dentro

        return {
            'dia': cls._totais(no_periodo(hoje)),
            'semana': cls._totais(no_periodo(semana)),
            'mes': cls._totais(no_periodo(mes)),
            'abertas': OrdemPolpa.objects.for_filial(filial).filter(
                situacao__in=OrdemPolpa.ABERTAS,
            ).count(),
            'concluidas_mes': len(no_periodo(mes)),
            'inicio_semana': semana,
            'inicio_mes': mes,
        }

    @staticmethod
    def _totais(ordens) -> dict:
        """
        Unidades e quilos. OS DOIS, porque a fábrica fala nos dois: vende em
        unidade e compra fruta em quilo, e converter de cabeça a cada
        conversa é como os números param de bater entre as áreas.
        """
        unidades = sum((o.quantidade_produzida or ZERO for o in ordens), ZERO)
        quilos = ZERO
        for op in ordens:
            peso = op.produto.peso_liquido or ZERO
            quilos += (op.quantidade_produzida or ZERO) * peso
        return {
            'ordens': len(ordens),
            'unidades': unidades,
            'kg': quilos.quantize(Decimal('0.001')),
        }

    # ══════════════════════════════════════════════════════════════════
    # EFICIÊNCIA
    # ══════════════════════════════════════════════════════════════════

    @classmethod
    def eficiencia(cls, filial, dias: int = 30) -> dict:
        """
        Rendimento, perda, produtividade, capacidade e tempo médio.

        A JANELA É MÓVEL (últimos 30 dias), e não o mês corrente: no dia 2
        do mês, "o mês" tem duas batidas e qualquer média vira ruído.
        """
        desde = timezone.now() - timedelta(days=dias)
        ordens = [
            op for op in (
                OrdemPolpa.objects.for_filial(filial)
                .filter(situacao=OrdemPolpa.Situacao.PRODUZIDA)
                .select_related('ordem', 'ordem__produto_acabado', 'receita')
            )
            if op.ordem.data_fim_real and op.ordem.data_fim_real >= desde
        ]

        planejado = sum((o.quantidade_planejada or ZERO for o in ordens), ZERO)
        produzido = sum((o.quantidade_produzida or ZERO for o in ordens), ZERO)

        # RENDIMENTO DO LOTE: entregou o que prometeu? É unidade sobre
        # unidade, e não peso — o de peso vive no processo, e misturar os
        # dois num número só faria os dois mentirem.
        rendimento = (
            (produzido / planejado * CEM).quantize(Decimal('0.01'))
            if planejado > ZERO else None
        )

        # PERDA DE PROCESSO: peso que entrou menos peso que saiu, somado
        # pelos apontamentos. Vem das etapas porque é lá que ela é medida.
        perdas = cls._perdas_de_processo(filial, desde)

        tempos = [
            o for o in ordens
            if o.ordem.data_inicio_real and o.ordem.data_fim_real
        ]
        tempo_medio = None
        if tempos:
            minutos = sum(
                (o.ordem.data_fim_real - o.ordem.data_inicio_real).total_seconds() / 60
                for o in tempos
            )
            tempo_medio = int(minutos / len(tempos))

        parados = sum((o.minutos_parados or 0 for o in ordens))

        # PRODUTIVIDADE: unidades por hora de produção. Sem tempo apontado
        # não há produtividade -- e um número inventado aqui viraria meta.
        horas = sum(
            (o.ordem.data_fim_real - o.ordem.data_inicio_real).total_seconds() / 3600
            for o in tempos
        )
        produtividade = (
            (produzido / Decimal(str(horas))).quantize(Decimal('0.01'))
            if horas > 0 else None
        )

        return {
            'dias': dias,
            'ordens': len(ordens),
            'rendimento': rendimento,
            'planejado': planejado,
            'produzido': produzido,
            'perda_processo': perdas['perda'],
            'perda_percentual': perdas['percentual'],
            'entrada_processo': perdas['entrada'],
            'tempo_medio_minutos': tempo_medio,
            'minutos_parados': parados,
            'produtividade': produtividade,
            'capacidade': cls._capacidade(filial),
        }

    @staticmethod
    def _perdas_de_processo(filial, desde) -> dict:
        """Quanto se perdeu nas etapas apontadas na janela."""
        etapas = (
            ApontamentoEtapa.objects.for_filial(filial)
            .filter(
                situacao=ApontamentoEtapa.Situacao.CONCLUIDA,
                concluida_em__gte=desde,
                quantidade_entrada__isnull=False,
                quantidade_saida__isnull=False,
            )
        )
        entrada = ZERO
        perda = ZERO
        for etapa in etapas:
            entrada += etapa.quantidade_entrada or ZERO
            perda += etapa.perda or ZERO

        return {
            'entrada': entrada,
            'perda': perda,
            'percentual': (
                (perda / entrada * CEM).quantize(Decimal('0.01'))
                if entrada > ZERO else None
            ),
        }

    @staticmethod
    def _capacidade(filial) -> dict | None:
        """
        Quanto da capacidade instalada está programada para os próximos sete
        dias. `None` sem recurso com capacidade cadastrada — não dá para
        dizer que se usa 80% do que ninguém mediu.
        """
        from apps.polpa.services.planejamento import PlanejamentoService

        hoje = timezone.localdate()
        dados = PlanejamentoService.carga_por_recurso(
            filial, hoje, hoje + timedelta(days=6),
        )[0]
        com_capacidade = [l for l in dados['linhas'] if l['disponivel']]
        if not com_capacidade:
            return None

        programado = sum((l['programado'] for l in com_capacidade), ZERO)
        disponivel = sum((l['disponivel'] for l in com_capacidade), ZERO)
        return {
            'programado': programado,
            'disponivel': disponivel,
            'percentual': (
                (programado / disponivel * CEM).quantize(Decimal('0.1'))
                if disponivel > ZERO else None
            ),
            'recursos': len(com_capacidade),
            'sem_capacidade': len(dados['linhas']) - len(com_capacidade),
        }

    # ══════════════════════════════════════════════════════════════════
    # CUSTOS
    # ══════════════════════════════════════════════════════════════════

    @classmethod
    def custos(cls, filial, dias: int = 30) -> dict:
        """
        Custo por kg, por unidade, por produto — e planejado contra realizado.

        AS CONTAS SÃO DO `CustoService`, que já existe e é usado na tela da
        ordem. Refazê-las aqui daria dois custos para a mesma batida, e o
        painel discordaria da própria ordem que ele resume.
        """
        from apps.polpa.services.custo import CustoService

        desde = timezone.now() - timedelta(days=dias)
        ordens = [
            op for op in (
                OrdemPolpa.objects.for_filial(filial)
                .filter(situacao=OrdemPolpa.Situacao.PRODUZIDA)
                .select_related('ordem', 'ordem__produto_acabado', 'receita')
            )
            if op.ordem.data_fim_real and op.ordem.data_fim_real >= desde
        ]

        por_produto: dict = {}
        total_real = ZERO
        total_previsto = ZERO
        unidades = ZERO
        quilos = ZERO

        for op in ordens:
            comparacao = CustoService.comparar(op)
            real = comparacao.get('realizado') or {}
            previsto = comparacao.get('previsto') or {}

            custo_real = real.get('total') or ZERO
            total_real += custo_real
            total_previsto += previsto.get('total') or ZERO

            quantidade = op.quantidade_produzida or ZERO
            peso = (op.produto.peso_liquido or ZERO) * quantidade
            unidades += quantidade
            quilos += peso

            chave = op.produto
            linha = por_produto.setdefault(chave, {
                'produto': chave, 'ordens': 0, 'custo': ZERO,
                'unidades': ZERO, 'kg': ZERO,
            })
            linha['ordens'] += 1
            linha['custo'] += custo_real
            linha['unidades'] += quantidade
            linha['kg'] += peso

        for linha in por_produto.values():
            linha['por_unidade'] = (
                (linha['custo'] / linha['unidades']).quantize(Decimal('0.0001'))
                if linha['unidades'] > ZERO else None
            )
            linha['por_kg'] = (
                (linha['custo'] / linha['kg']).quantize(Decimal('0.0001'))
                if linha['kg'] > ZERO else None
            )

        desvio = None
        if total_previsto > ZERO:
            desvio = (
                (total_real - total_previsto) / total_previsto * CEM
            ).quantize(Decimal('0.01'))

        return {
            'dias': dias,
            'ordens': len(ordens),
            'total_real': total_real.quantize(Decimal('0.01')),
            'total_previsto': total_previsto.quantize(Decimal('0.01')),
            'desvio': desvio,
            'por_unidade': (
                (total_real / unidades).quantize(Decimal('0.0001'))
                if unidades > ZERO else None
            ),
            'por_kg': (
                (total_real / quilos).quantize(Decimal('0.0001'))
                if quilos > ZERO else None
            ),
            'por_produto': sorted(
                por_produto.values(), key=lambda l: l['custo'], reverse=True,
            ),
            'materia_prima': cls._evolucao_materia_prima(filial, dias),
        }

    @staticmethod
    def _evolucao_materia_prima(filial, dias: int) -> list[dict]:
        """
        O custo médio de compra da matéria-prima, mês a mês.

        SAI DAS ENTRADAS DE ESTOQUE, que é onde o preço realmente pago está.
        O `preco_custo` do cadastro é o número que alguém digitou uma vez —
        serve de referência, não de histórico.
        """
        desde = timezone.now() - timedelta(days=max(dias, 90))
        materias = set(
            FichaProduto.objects.for_filial(filial)
            .filter(classe=FichaProduto.Classe.MATERIA_PRIMA)
            .values_list('produto_id', flat=True)
        )
        if not materias:
            return []

        entradas = (
            MovimentacaoEstoque.objects
            .filter(
                filial=filial, produto_id__in=materias,
                created_at__gte=desde,
                tipo_operacao__in=(
                    MovimentacaoEstoque.TipoOperacao.ENTRADA,
                    MovimentacaoEstoque.TipoOperacao.PRODUCAO_ENTRADA,
                ),
                valor_unitario__gt=0,
            )
            .select_related('produto')
        )

        por_mes: dict = {}
        for mov in entradas:
            chave = timezone.localtime(mov.created_at).strftime('%Y-%m')
            linha = por_mes.setdefault(chave, {
                'mes': chave, 'quantidade': ZERO, 'valor': ZERO,
            })
            quantidade = mov.quantidade or ZERO
            linha['quantidade'] += quantidade
            linha['valor'] += quantidade * (mov.valor_unitario or ZERO)

        linhas = []
        for chave in sorted(por_mes):
            linha = por_mes[chave]
            linha['medio'] = (
                (linha['valor'] / linha['quantidade']).quantize(Decimal('0.0001'))
                if linha['quantidade'] > ZERO else None
            )
            linhas.append(linha)
        return linhas

    # ══════════════════════════════════════════════════════════════════
    # ESTOQUE
    # ══════════════════════════════════════════════════════════════════

    @classmethod
    def estoque(cls, filial, dias: int = 30) -> dict:
        """
        Matéria-prima, acabado, validade, parado e giro.

        O GIRO É CONSUMO SOBRE SALDO, na janela. Sem consumo é `None` e não
        zero: "não girou" e "não teve saída registrada" levam a decisões
        opostas -- a primeira manda parar de comprar, a segunda manda
        conferir o apontamento.
        """
        acabado = ArmazenagemService.resumo(filial)

        materias = set(
            FichaProduto.objects.for_filial(filial)
            .filter(classe=FichaProduto.Classe.MATERIA_PRIMA)
            .values_list('produto_id', flat=True)
        )
        saldo_mp = (
            Estoque.objects.filter(filial=filial, produto_id__in=materias)
            .aggregate(total=Sum('quantidade_disponivel'))['total'] or ZERO
        )
        valor_mp = ZERO
        for estoque in Estoque.objects.filter(
            filial=filial, produto_id__in=materias,
        ).select_related('produto'):
            valor_mp += (estoque.quantidade_disponivel or ZERO) * (
                estoque.custo_medio or estoque.produto.preco_custo_medio or ZERO
            )

        return {
            'materia_prima': {
                'itens': len(materias),
                'saldo': saldo_mp,
                'valor': valor_mp.quantize(Decimal('0.01')),
            },
            'acabado': acabado,
            'parados': cls._parados(filial),
            'giro': cls._giro(filial, dias),
            'dias': dias,
        }

    @staticmethod
    def _parados(filial) -> list[dict]:
        """
        Lotes com saldo e sem saída há mais de um mês.

        É O ESTOQUE QUE NINGUÉM VÊ: não vence hoje, não falta, e ocupa
        câmara. Aparece quando alguém procura — e ninguém procura.
        """
        limite = timezone.now() - timedelta(days=DIAS_PARADO)
        parados = []

        for lote in (
            LoteProduto.objects.filter(filial=filial, quantidade_atual__gt=0)
            .exclude(status=LoteProduto.Status.ESGOTADO)
            .select_related('produto')
        ):
            # SAÍDA, e não qualquer movimentação: a ENTRADA do lote é o
            # nascimento dele, e contá-la faria todo lote recém-produzido
            # parecer que girou — justamente o contrário do que se procura.
            saiu = (
                MovimentacaoEstoque.objects
                .filter(
                    lote=lote, created_at__gte=limite,
                    tipo_operacao__in=(
                        MovimentacaoEstoque.TipoOperacao.SAIDA,
                        MovimentacaoEstoque.TipoOperacao.PRODUCAO_SAIDA,
                        MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
                        MovimentacaoEstoque.TipoOperacao.AJUSTE_MENOS,
                        MovimentacaoEstoque.TipoOperacao.PERDA,
                        MovimentacaoEstoque.TipoOperacao.BAIXA_VALIDADE,
                    ),
                )
                .exists()
            )
            if saiu:
                continue
            parados.append({
                'lote': lote,
                # `localtime` antes de `.date()`: `localdate()` e' data LOCAL
                # e `created_at` e' gravado em UTC. Comparar os dois direto
                # erra por um dia das 21h a` meia-noite, quando a data UTC ja'
                # virou e a local nao -- e erra para MENOS, deixando o lote
                # mais parado parecer mais novo do que e'. Justamente o que
                # esta lista existe para nao deixar acontecer.
                'dias': (
                    (timezone.localdate()
                     - timezone.localtime(lote.created_at).date()).days
                    if lote.created_at else None
                ),
            })
        parados.sort(key=lambda p: p['dias'] or 0, reverse=True)
        return parados[:20]

    @staticmethod
    def _giro(filial, dias: int) -> dict:
        """Consumo do período sobre o saldo atual — as duas parcelas à vista."""
        desde = timezone.now() - timedelta(days=dias)
        consumo = (
            MovimentacaoEstoque.objects
            .filter(
                filial=filial, created_at__gte=desde,
                tipo_operacao__in=(
                    MovimentacaoEstoque.TipoOperacao.PRODUCAO_SAIDA,
                    MovimentacaoEstoque.TipoOperacao.SAIDA,
                ),
            )
            .aggregate(total=Sum('quantidade'))['total'] or ZERO
        )
        saldo = (
            Estoque.objects.filter(filial=filial)
            .aggregate(total=Sum('quantidade_disponivel'))['total'] or ZERO
        )
        return {
            'consumo': consumo,
            'saldo': saldo,
            'indice': (
                (consumo / saldo).quantize(Decimal('0.01'))
                if saldo > ZERO and consumo > ZERO else None
            ),
        }

    # ══════════════════════════════════════════════════════════════════
    # QUALIDADE
    # ══════════════════════════════════════════════════════════════════

    @classmethod
    def qualidade(cls, filial, dias: int = 30) -> dict:
        """
        Aprovados, reprovados, não conformidades e o índice de perdas.

        AS DUAS FONTES CONVIVEM: a análise da qualidade (`AnaliseQualidade`,
        com Brix e pH) e a inspeção do lote (`InspecaoLote`, o parecer de
        quem olhou). Somar as duas num número só esconderia qual delas
        reprovou — e são áreas diferentes que respondem por cada uma.
        """
        from apps.lotes.models import InspecaoLote
        from apps.qualidade.constants.enums import ResultadoAnalise
        from apps.qualidade.models import AnaliseQualidade

        desde = timezone.now() - timedelta(days=dias)

        analises = AnaliseQualidade.objects.filter(
            filial=filial, data_analise__gte=desde,
        )
        inspecoes = InspecaoLote.objects.filter(
            lote__filial=filial, data_inspecao__gte=desde,
        )

        aprovadas = analises.filter(
            resultado__in=(
                ResultadoAnalise.APROVADO, ResultadoAnalise.APROVADO_COM_RESSALVA,
            ),
        ).count()
        reprovadas = analises.filter(resultado=ResultadoAnalise.REPROVADO).count()

        perdas = cls._perdas_de_processo(filial, desde)

        return {
            'dias': dias,
            'analises': analises.count(),
            'aprovadas': aprovadas,
            'reprovadas': reprovadas,
            'pendentes': analises.filter(
                resultado=ResultadoAnalise.PENDENTE,
            ).count(),
            'taxa_aprovacao': (
                (Decimal(aprovadas) / Decimal(aprovadas + reprovadas) * CEM)
                .quantize(Decimal('0.01'))
                if (aprovadas + reprovadas) else None
            ),
            'inspecoes': inspecoes.count(),
            'inspecoes_reprovadas': inspecoes.filter(
                resultado=InspecaoLote.Resultado.REPROVADO,
            ).count(),
            'quarentena': inspecoes.filter(
                resultado=InspecaoLote.Resultado.QUARENTENA,
            ).count(),
            'bloqueados': LoteProduto.objects.filter(
                filial=filial, status=LoteProduto.Status.BLOQUEADO,
                quantidade_atual__gt=0,
            ).count(),
            'indice_perdas': perdas['percentual'],
        }

    # ══════════════════════════════════════════════════════════════════

    @classmethod
    def painel(cls, filial, dias: int = 30) -> dict:
        """As cinco perguntas, juntas."""
        return {
            'producao': cls.producao(filial),
            'eficiencia': cls.eficiencia(filial, dias),
            'custos': cls.custos(filial, dias),
            'estoque': cls.estoque(filial, dias),
            'qualidade': cls.qualidade(filial, dias),
            'dias': dias,
        }
