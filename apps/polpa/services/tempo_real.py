"""
O dia da fábrica, agora — o painel que fica na parede.

ESTA TELA TEM OUTRO DONO. O painel industrial (seção 18) é de quem decide:
janela de 30 dias, custo, giro, tendência. Este é de quem está PRODUZINDO
hoje, e a pergunta dele é uma só: "estamos no ritmo?". Por isso aqui não
tem mês, não tem custo e não tem tendência — tem meta, produzido e o que
está rodando neste minuto.

O RITMO IMPORTA TANTO QUANTO O TOTAL. Às 10h da manhã, 3.000 kg de uma meta
de 10.000 podem ser ótimo (a fábrica só começou às 8h) ou péssimo (deveria
ter feito 5.000). Por isso o painel mostra o ESPERADO ATÉ AGORA junto do
atingimento: sem ele, o número só vira cobrança às 17h, quando não dá mais
para reagir.

SEM META CADASTRADA, NÃO SE INVENTA UMA. O atingimento fica nulo e a tela
diz onde cadastrar. Assumir zero faria qualquer produção parecer infinita;
assumir um número faria a fábrica ser cobrada por uma meta que ninguém
combinou.
"""
from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.polpa.models import ApontamentoEtapa, MetaProducao, OrdemPolpa

ZERO = Decimal('0')
CEM = Decimal('100')

# O turno padrão para calcular o ritmo esperado. Não é uma regra de negócio
# escondida: é a referência de "quanto do dia já passou", e a fábrica que
# roda em dois turnos vai ver o esperado subir mais devagar do que produz —
# o que é visível na tela, não silencioso.
INICIO_TURNO = time(7, 0)
FIM_TURNO = time(17, 0)


class TempoRealService:

    @classmethod
    def hoje(cls, filial, agora=None) -> dict:
        """O dia inteiro numa consulta."""
        agora = agora or timezone.localtime()
        dia = agora.date()

        ordens = list(
            OrdemPolpa.objects.for_filial(filial)
            .select_related('ordem', 'ordem__produto_acabado', 'recurso', 'responsavel')
        )

        # PRODUZIDAS HOJE: as que terminaram hoje. A data de término é a que
        # marca o dia da produção -- uma ordem aberta ontem e fechada hoje
        # produziu hoje, e é hoje que ela conta para a meta.
        produzidas = [
            op for op in ordens
            if op.situacao == OrdemPolpa.Situacao.PRODUZIDA
            and op.ordem.data_fim_real
            and timezone.localtime(op.ordem.data_fim_real).date() == dia
        ]
        em_producao = [
            op for op in ordens
            if op.situacao in (
                OrdemPolpa.Situacao.EM_PRODUCAO, OrdemPolpa.Situacao.QUALIDADE,
            )
        ]
        pausadas = [op for op in ordens if op.situacao == OrdemPolpa.Situacao.PAUSADA]
        planejadas = [
            op for op in ordens
            if op.situacao in (
                OrdemPolpa.Situacao.PLANEJADA, OrdemPolpa.Situacao.LIBERADA,
            )
        ]

        kg = ZERO
        unidades = ZERO
        for op in produzidas:
            quantidade = op.quantidade_produzida or ZERO
            unidades += quantidade
            kg += quantidade * (op.produto.peso_liquido or ZERO)
        kg = kg.quantize(Decimal('0.001'))

        meta = MetaProducao.do_dia(filial, dia)
        atingimento = None
        if meta and meta.meta_kg > ZERO:
            atingimento = (kg / meta.meta_kg * CEM).quantize(Decimal('0.1'))

        return {
            'dia': dia,
            'agora': agora,
            'planejadas': planejadas,
            'em_producao': em_producao,
            'pausadas': pausadas,
            'produzidas': produzidas,
            'kg': kg,
            'unidades': unidades,
            'meta': meta,
            'atingimento': atingimento,
            'ritmo': cls._ritmo(meta, kg, agora),
            'perdas': cls._perdas(filial, dia),
            'rendimento': cls._rendimento(produzidas),
            'por_hora': cls._por_hora(produzidas),
        }

    # ── Ritmo ────────────────────────────────────────────────────────────

    @classmethod
    def _ritmo(cls, meta, kg, agora) -> dict | None:
        """
        Quanto já deveria estar pronto a esta hora.

        É O QUE FAZ O PAINEL SERVIR DE MANHÃ. Só o atingimento do dia
        transforma a tela em cobrança das 17h; com o esperado até agora, às
        10h já dá para decidir se chama gente ou muda a ordem.
        """
        if meta is None or meta.meta_kg <= ZERO:
            return None

        inicio = timezone.make_aware(
            timezone.datetime.combine(agora.date(), INICIO_TURNO),
            agora.tzinfo,
        )
        fim = timezone.make_aware(
            timezone.datetime.combine(agora.date(), FIM_TURNO),
            agora.tzinfo,
        )
        total = (fim - inicio).total_seconds()
        decorrido = max(min((agora - inicio).total_seconds(), total), 0)
        fracao = Decimal(str(decorrido / total)) if total else ZERO

        esperado = (meta.meta_kg * fracao).quantize(Decimal('0.001'))
        return {
            'esperado': esperado,
            'diferenca': (kg - esperado).quantize(Decimal('0.001')),
            'no_ritmo': kg >= esperado,
            'percentual_do_turno': (fracao * CEM).quantize(Decimal('0.1')),
            'inicio': inicio,
            'fim': fim,
        }

    # ── Perdas e rendimento ──────────────────────────────────────────────

    @staticmethod
    def _perdas(filial, dia) -> dict:
        """As perdas apontadas hoje, pelas etapas concluídas."""
        inicio = timezone.make_aware(
            timezone.datetime.combine(dia, time.min), timezone.get_current_timezone(),
        )
        etapas = (
            ApontamentoEtapa.objects.for_filial(filial)
            .filter(
                situacao=ApontamentoEtapa.Situacao.CONCLUIDA,
                concluida_em__gte=inicio,
                quantidade_entrada__isnull=False,
                quantidade_saida__isnull=False,
            )
            .select_related('ordem', 'ordem__ordem')
        )

        entrada = ZERO
        perda = ZERO
        piores = []
        for etapa in etapas:
            entrada += etapa.quantidade_entrada or ZERO
            atual = etapa.perda or ZERO
            perda += atual
            if atual:
                piores.append({'etapa': etapa, 'perda': atual})

        piores.sort(key=lambda p: p['perda'], reverse=True)
        return {
            'entrada': entrada,
            'perda': perda,
            'percentual': (
                (perda / entrada * CEM).quantize(Decimal('0.01'))
                if entrada > ZERO else None
            ),
            'maiores': piores[:3],
        }

    @staticmethod
    def _rendimento(produzidas) -> Decimal | None:
        """
        Produzido sobre planejado nas ordens que fecharam hoje.

        `None` sem ordem fechada: zero seria "não rendeu nada", e às 9h da
        manhã toda fábrica apareceria em colapso.
        """
        planejado = sum((op.quantidade_planejada or ZERO for op in produzidas), ZERO)
        produzido = sum((op.quantidade_produzida or ZERO for op in produzidas), ZERO)
        if planejado <= ZERO:
            return None
        return (produzido / planejado * CEM).quantize(Decimal('0.01'))

    # ── Gráfico ──────────────────────────────────────────────────────────

    @staticmethod
    def _por_hora(produzidas) -> list[dict]:
        """
        Os quilos fechados em cada hora do turno — as barras do gráfico.

        POR HORA DE TÉRMINO, que é quando a produção existe de fato. Ratear
        pelo tempo da ordem daria um gráfico bonito e falso: mostraria
        produção em horas em que nada saiu da linha.
        """
        horas: dict = {}
        for op in produzidas:
            hora = timezone.localtime(op.ordem.data_fim_real).hour
            peso = (op.quantidade_produzida or ZERO) * (op.produto.peso_liquido or ZERO)
            horas[hora] = horas.get(hora, ZERO) + peso

        if not horas:
            return []

        maior = max(horas.values()) or Decimal('1')
        return [
            {
                'hora': hora,
                'kg': horas[hora].quantize(Decimal('0.001')),
                # A ALTURA É RELATIVA À MAIOR BARRA, e não à meta: numa hora
                # ruim todas as barras ficariam invisíveis, e o gráfico
                # deixaria de mostrar a diferença entre as horas — que é
                # justamente o que ele existe para mostrar.
                'altura': int(horas[hora] / maior * 100),
            }
            for hora in sorted(horas)
        ]
