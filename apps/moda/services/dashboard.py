"""
Dashboard executivo do vertical — os números que a direção olha de manhã.

DUAS NATUREZAS DE NÚMERO, e misturá-las é a mentira clássica de dashboard:

  · FLUXO — o que aconteceu no período escolhido: pedidos entrados, peças
    produzidas, faturamento, perdas. Muda quando se troca o filtro.
  · ESTOQUE — a foto de agora: WIP, pedidos atrasados, entregas próximas,
    capacidade. NÃO muda com o filtro, porque "WIP dos últimos 30 dias" não
    quer dizer nada — ou a peça está no chão de fábrica agora, ou não está.

Cada indicador declara o que é, e a tela separa os dois blocos. Sem isso,
alguém filtra "últimos 7 dias", lê WIP de 500 peças e conclui que entraram
500 peças na semana.

TUDO É DERIVADO, nada é gravado. Não existe tabela de indicadores: pedido,
ordem, etapa do fluxo, corte e capacidade já estão no banco, e um número
consolidado à parte começaria a divergir da origem no primeiro apontamento
corrigido à mão.

ONDE A CONTA NÃO PODE SER FEITA, ELA NÃO É INVENTADA. Margem sem ficha
técnica cadastrada daria 100%, e 100% de margem numa confecção é um número
que ninguém questiona e todo mundo repete. Por isso os indicadores de custo
dizem sobre quantas ordens foram calculados — e quando a base é vazia,
dizem que é vazia em vez de mostrar zero.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from apps.moda.models import (
    CapacidadeSetor, EtapaOrdem, OrdemProducao, PedidoProducao, RegistroCorte,
)
from apps.moda.services.wip import WipService

CEM = Decimal('100')
ZERO = Decimal('0')

# Janelas oferecidas no filtro. Mais do que isso vira menu de configuração;
# estas três respondem "esta semana", "este mês" e "o trimestre".
PERIODOS = ((7, 'Últimos 7 dias'), (30, 'Últimos 30 dias'), (90, 'Últimos 90 dias'))
PERIODO_PADRAO = 30

# Entrega "próxima" é a da semana: é o horizonte em que ainda dá para
# reagir. Mais longe que isso não é urgência, é planejamento — e o PCP já
# tem tela para planejamento.
DIAS_ENTREGA_PROXIMA = 7


@dataclass
class Indicador:
    """Um número do painel, com o que ele significa."""
    chave: str
    label: str
    valor: str
    # Segunda linha do card: a ressalva, a base de cálculo ou a comparação.
    # É onde mora a honestidade do número.
    detalhe: str = ''
    tom: str = 'neutro'  # neutro | bom | atencao | ruim
    # False = foto de agora, indiferente ao filtro de período.
    do_periodo: bool = True


@dataclass
class Fatia:
    """Uma barra de gráfico."""
    label: str
    valor: Decimal
    texto: str
    tom: str = 'neutro'

    @property
    def valor_float(self) -> float:
        return float(self.valor)


@dataclass
class Grafico:
    chave: str
    titulo: str
    ajuda: str
    fatias: list[Fatia] = field(default_factory=list)
    unidade: str = ''
    # 'barras' = série no tempo (vertical); 'categorias' = comparação
    # (horizontal, com o rótulo legível ao lado).
    tipo: str = 'categorias'

    @property
    def vazio(self) -> bool:
        return not self.fatias or all(f.valor == 0 for f in self.fatias)

    @property
    def maximo(self) -> float:
        return max([float(f.valor) for f in self.fatias] or [0]) or 1.0

    @property
    def barras(self) -> list[tuple]:
        """
        Pares (fatia, tamanho em %) — o template não sabe fazer a regra de três.

        Django não deixa chamar método com argumento no template, então a
        proporção de cada barra tem que sair pronta daqui. É também o lugar
        certo: a escala é do gráfico, não da fatia.
        """
        maximo = self.maximo
        return [
            (f, round(f.valor_float / maximo * 100, 1)) for f in self.fatias
        ]


def _pct(parte, total) -> Decimal:
    if not total:
        return ZERO
    return (Decimal(parte) / Decimal(total) * CEM).quantize(Decimal('0.1'))


def _dinheiro(valor: Decimal) -> str:
    inteiro = f'{valor:,.2f}'
    # pt-BR: o separador de milhar é ponto e o decimal é vírgula. A troca em
    # duas etapas evita o vaivém de trocar ponto por vírgula e de volta.
    return 'R$ ' + inteiro.replace(',', '#').replace('.', ',').replace('#', '.')


class DashboardService:

    # ── Entrada ──────────────────────────────────────────────────────────

    @classmethod
    def painel(cls, filial, dias: int = PERIODO_PADRAO, hoje: date | None = None) -> dict:
        hoje = hoje or timezone.localdate()
        dias = dias if dias in {d for d, _ in PERIODOS} else PERIODO_PADRAO
        inicio = hoje - timedelta(days=dias - 1)

        pedidos = cls._pedidos_do_periodo(filial, inicio, hoje)
        ordens = cls._ordens_do_periodo(filial, inicio, hoje)
        etapas = cls._etapas_concluidas(filial, inicio, hoje)
        cortes = cls._cortes_do_periodo(filial, inicio, hoje)
        wip = WipService.painel(filial)
        concluidas = cls._ordens_concluidas(filial, inicio, hoje)

        return {
            'dias': dias,
            'inicio': inicio,
            'fim': hoje,
            'periodos': PERIODOS,
            'indicadores': cls._indicadores(
                filial, pedidos, ordens, etapas, cortes, wip, concluidas,
                dias, hoje,
            ),
            'graficos': cls._graficos(
                filial, pedidos, etapas, cortes, inicio, hoje,
            ),
        }

    # ── Bases ────────────────────────────────────────────────────────────

    @staticmethod
    def _pedidos_do_periodo(filial, inicio, fim):
        """
        Pedidos ENTRADOS no período — pela data do pedido, não pela entrega.

        Orçamento fica de fora de tudo que é volume ou dinheiro: proposta
        não é venda, e contá-la infla faturamento com pedido que talvez
        nunca exista.
        """
        return list(
            PedidoProducao.objects.for_filial(filial)
            .exclude(status=PedidoProducao.Status.ORCAMENTO)
            .filter(data_pedido__gte=inicio, data_pedido__lte=fim)
            .select_related('cliente')
            .prefetch_related('itens__produto__colecao')
        )

    @staticmethod
    def _ordens_do_periodo(filial, inicio, fim):
        return list(
            OrdemProducao.objects.for_filial(filial)
            .filter(emitida_em__date__gte=inicio, emitida_em__date__lte=fim)
            .select_related(
                'item', 'item__produto', 'item__produto__colecao',
                'item__produto__ficha', 'item__produto__roteiro',
            )
        )

    @staticmethod
    def _etapas_concluidas(filial, inicio, fim):
        """Etapas fechadas no período — a produção que de fato aconteceu."""
        return list(
            EtapaOrdem.objects
            .filter(
                ordem__filial=filial,
                status=EtapaOrdem.Status.CONCLUIDA,
                data_conclusao__gte=inicio,
                data_conclusao__lte=fim,
            )
            .select_related('ordem')
        )

    @staticmethod
    def _cortes_do_periodo(filial, inicio, fim):
        return list(
            RegistroCorte.objects.for_filial(filial)
            .filter(status=RegistroCorte.Status.CORTADO)
            .filter(data__gte=inicio, data__lte=fim)
            .select_related('encaixe')
        )

    # ── Indicadores ──────────────────────────────────────────────────────

    @classmethod
    def _indicadores(cls, filial, pedidos, ordens, etapas, cortes, wip,
                     concluidas, dias, hoje) -> list:
        pecas_vendidas = sum(p.quantidade_total for p in pedidos)
        faturamento = sum((p.valor_total for p in pedidos), ZERO)

        produzidas = sum(e.quantidade_produzida for e in etapas)
        perdidas = sum(e.perda for e in etapas)

        custo, margem, base_custo = cls._custo_e_margem(ordens)
        aproveitamento, metros = cls._aproveitamento(cortes)
        eficiencia, base_eficiencia = cls._eficiencia(concluidas)

        abertos = cls._pedidos_abertos(filial)
        atrasados = [p for p in abertos if p.atrasado]
        proximos = [
            p for p in abertos
            if p.data_prevista_entrega
            and hoje <= p.data_prevista_entrega <= hoje + timedelta(days=DIAS_ENTREGA_PROXIMA)
        ]

        em_producao = cls._em_producao(wip)
        prontas = cls._pecas_do_balde(wip, 'prontas')
        total_wip = sum(c.pecas for c in wip['colunas'])

        return [
            # ── Comercial ────────────────────────────────────────────────
            Indicador('pedidos', 'Pedidos', str(len(pedidos)),
                      f'entrados nos últimos {dias} dias'),
            Indicador('pecas_vendidas', 'Peças vendidas', f'{pecas_vendidas:,}'.replace(',', '.'),
                      'somadas as grades dos pedidos'),
            Indicador('faturamento', 'Faturamento', _dinheiro(faturamento),
                      'valor dos pedidos fechados no período — não é nota emitida'),
            *cls._margem_indicadores(margem, custo, base_custo, len(ordens)),

            # ── Produção no período ──────────────────────────────────────
            Indicador('producao_diaria', 'Produção diária',
                      f'{produzidas // max(dias, 1):,}'.replace(',', '.') + ' peças/dia',
                      f'{produzidas:,}'.replace(',', '.') + f' peças em {dias} dias'),
            cls._setor_lider(etapas),
            *cls._eficiencia_indicador(eficiencia, base_eficiencia),
            *cls._aproveitamento_indicador(aproveitamento, cortes),
            cls._perdas_indicador(perdidas, produzidas, metros),

            # ── Foto de agora ────────────────────────────────────────────
            Indicador('em_producao', 'Peças em produção', f'{em_producao:,}'.replace(',', '.'),
                      'do corte ao acabamento, agora', do_periodo=False),
            Indicador('prontas', 'Peças prontas', f'{prontas:,}'.replace(',', '.'),
                      'aguardando embalagem ou expedição', do_periodo=False,
                      tom='bom' if prontas else 'neutro'),
            Indicador('wip', 'WIP total', f'{total_wip:,}'.replace(',', '.'),
                      'todas as peças no chão de fábrica', do_periodo=False),
            Indicador('atrasados', 'Pedidos atrasados', str(len(atrasados)),
                      cls._resumo_atraso(atrasados), do_periodo=False,
                      tom='ruim' if atrasados else 'bom'),
            Indicador('entregas_proximas', 'Entregas próximas', str(len(proximos)),
                      f'nos próximos {DIAS_ENTREGA_PROXIMA} dias', do_periodo=False,
                      tom='atencao' if proximos else 'neutro'),
            *cls._capacidade_indicador(filial),
        ]

    # ── Indicadores que precisam declarar a base ─────────────────────────

    @staticmethod
    def _margem_indicadores(margem, custo, base, total_ordens) -> list:
        """
        Custo por peça e margem — ou o aviso de que não dá para calcular.

        Sem ficha técnica e roteiro, o custo de uma ordem é zero, e zero de
        custo vira 100% de margem. Um número desses não é aproximação: é
        errado, e ninguém questiona uma margem alta.
        """
        if not base:
            aviso = ('nenhuma ordem do período tem ficha técnica e roteiro '
                     'cadastrados')
            return [
                Indicador('custo_peca', 'Custo por peça', '—', aviso, tom='atencao'),
                Indicador('margem', 'Margem', '—', aviso, tom='atencao'),
            ]

        cobertura = (f'sobre {base} de {total_ordens} ordens'
                     if base < total_ordens else f'sobre as {base} ordens do período')
        tom = 'ruim' if margem < 10 else 'bom' if margem >= 25 else 'atencao'
        return [
            Indicador('custo_peca', 'Custo por peça', _dinheiro(custo), cobertura),
            Indicador('margem', 'Margem', f'{margem:.1f}%'.replace('.', ','),
                      cobertura, tom=tom),
        ]

    @staticmethod
    def _eficiencia_indicador(eficiencia, base) -> list:
        if not base:
            return [Indicador(
                'eficiencia', 'Eficiência', '—',
                'nenhuma etapa concluída no período tem tempo apontado',
                tom='atencao',
            )]
        tom = 'bom' if eficiencia >= 90 else 'atencao' if eficiencia >= 70 else 'ruim'
        return [Indicador(
            'eficiencia', 'Eficiência', f'{eficiencia:.1f}%'.replace('.', ','),
            f'tempo previsto ÷ apontado, em {base} ordens concluídas', tom=tom,
        )]

    @staticmethod
    def _aproveitamento_indicador(aproveitamento, cortes) -> list:
        if not cortes:
            return [Indicador(
                'aproveitamento', 'Aproveitamento de corte', '—',
                'nenhum corte registrado no período', tom='atencao',
            )]
        tom = 'bom' if aproveitamento >= 85 else 'atencao' if aproveitamento >= 75 else 'ruim'
        return [Indicador(
            'aproveitamento', 'Aproveitamento de corte',
            f'{aproveitamento:.1f}%'.replace('.', ','),
            f'média ponderada pelo tecido de {len(cortes)} cortes', tom=tom,
        )]

    @staticmethod
    def _setor_lider(etapas) -> Indicador:
        """
        Qual setor mais produziu no período — o gráfico mostra todos.

        O card responde "quem puxou a produção"; o gráfico responde "como
        ela se distribuiu". São perguntas diferentes, e quem passa os olhos
        pelo topo da tela só faz a primeira.
        """
        por_etapa = defaultdict(int)
        for e in etapas:
            por_etapa[e.etapa] += e.quantidade_produzida

        if not por_etapa:
            return Indicador('producao_setor', 'Produção por setor', '—',
                             'nenhuma etapa concluída no período', tom='atencao')

        rotulos = dict(EtapaOrdem.Etapa.choices)
        chave, pecas = max(por_etapa.items(), key=lambda x: x[1])
        total = sum(por_etapa.values())
        return Indicador(
            'producao_setor', 'Produção por setor', rotulos.get(chave, chave),
            f'{pecas:,}'.replace(',', '.') +
            f' peças · {_pct(pecas, total):.0f}% do total'.replace('.', ','),
        )

    @staticmethod
    def _perdas_indicador(perdidas, produzidas, metros) -> Indicador:
        percentual = _pct(perdidas, produzidas + perdidas)
        detalhe = f'{percentual:.1f}% do produzido'.replace('.', ',')
        if metros:
            detalhe += f' · {metros:.1f} m de tecido'.replace('.', ',')
        tom = 'ruim' if percentual >= 5 else 'atencao' if percentual >= 2 else 'bom'
        return Indicador('perdas', 'Perdas', f'{perdidas:,}'.replace(',', '.') + ' peças',
                         detalhe, tom=tom)

    @classmethod
    def _capacidade_indicador(cls, filial) -> list:
        """
        Capacidade instalada por semana, e quanto dela está comprometida.

        A carga vem do PCP, que já sabe somar o roteiro das ordens abertas.
        Refazer a conta aqui daria dois números diferentes para a mesma
        pergunta na mesma tela.
        """
        capacidades = list(CapacidadeSetor.objects.for_filial(filial))
        if not capacidades:
            return [Indicador(
                'capacidade', 'Capacidade', '—',
                'nenhum setor com capacidade cadastrada', tom='atencao',
                do_periodo=False,
            )]

        horas = sum((c.horas_semana for c in capacidades), ZERO)
        return [Indicador(
            'capacidade', 'Capacidade', f'{horas:.0f} h/semana',
            f'{len(capacidades)} setores cadastrados', do_periodo=False,
        )]

    # ── Contas ───────────────────────────────────────────────────────────

    @staticmethod
    def _custo_e_margem(ordens) -> tuple[Decimal, Decimal, int]:
        """
        Custo médio por peça e margem — só sobre ordens que têm como calcular.

        Devolve também QUANTAS ordens entraram na conta: é o que permite a
        tela dizer "sobre 12 de 40 ordens" em vez de fingir que cobre tudo.
        """
        com_custo = [o for o in ordens if o.custo_total > 0 and o.quantidade]
        if not com_custo:
            return ZERO, ZERO, 0

        custo_total = sum((o.custo_total for o in com_custo), ZERO)
        pecas = sum(o.quantidade for o in com_custo)
        custo_peca = (custo_total / pecas).quantize(Decimal('0.01'))

        # Receita das MESMAS ordens, para a margem comparar o comparável:
        # o preço do item do pedido que gerou cada ordem.
        receita = sum(
            (Decimal(o.quantidade) * (o.item.valor_unitario or ZERO) for o in com_custo),
            ZERO,
        )
        if receita <= 0:
            return custo_peca, ZERO, len(com_custo)

        margem = ((receita - custo_total) / receita * CEM).quantize(Decimal('0.1'))
        return custo_peca, margem, len(com_custo)

    @staticmethod
    def _aproveitamento(cortes) -> tuple[Decimal, Decimal]:
        """
        Média PONDERADA pelo tecido gasto, e os metros perdidos.

        Média simples trataria um corte de 2 m igual a um de 200 m — e é o
        de 200 que decide o custo do mês.
        """
        medidos = [c for c in cortes if c.aproveitamento_efetivo > 0]
        metros = sum((c.perda_metros for c in cortes), ZERO)
        if not medidos:
            return ZERO, metros

        peso_total = sum((c.consumo_real or ZERO) for c in medidos)
        if peso_total <= 0:
            simples = sum((c.aproveitamento_efetivo for c in medidos), ZERO) / len(medidos)
            return simples.quantize(Decimal('0.1')), metros

        soma = sum(
            (c.aproveitamento_efetivo * (c.consumo_real or ZERO) for c in medidos), ZERO,
        )
        return (soma / peso_total).quantize(Decimal('0.1')), metros

    @staticmethod
    def _eficiencia(ordens_concluidas) -> tuple[Decimal, int]:
        """
        Tempo previsto ÷ tempo apontado, ORDEM a ordem.

        A comparação é no nível da ordem inteira, e não de cada etapa, por
        uma razão de unidade: o roteiro dá o tempo padrão da PEÇA PRONTA,
        somando todas as operações. Confrontá-lo com o apontamento de uma
        etapa só compararia o tempo do produto inteiro com o de um posto, e
        a eficiência sairia várias vezes maior que a real.

        Só entram ordens com fluxo concluído, roteiro cadastrado e tempo
        apontado. Ordem sem apontamento entraria como tempo zero e mandaria
        a eficiência para o infinito.
        """
        previsto = ZERO
        apontado = ZERO
        base = 0

        for ordem, minutos in ordens_concluidas:
            roteiro = ordem.roteiro
            if roteiro is None or minutos <= 0 or not ordem.quantidade:
                continue
            padrao = roteiro.tempo_total * ordem.quantidade
            if padrao <= 0:
                continue
            previsto += padrao
            apontado += minutos
            base += 1

        if not base or apontado <= 0:
            return ZERO, 0
        return (previsto / apontado * CEM).quantize(Decimal('0.1')), base

    @classmethod
    def _ordens_concluidas(cls, filial, inicio, fim) -> list:
        """
        Ordens cuja ENTREGA foi concluída no período, com o tempo apontado.

        O par (ordem, minutos) sai daqui pronto porque o tempo vem de outra
        tabela: somar as etapas de cada ordem dentro do laço da eficiência
        faria uma consulta por ordem.
        """
        etapas_finais = (
            EtapaOrdem.objects
            .filter(
                ordem__filial=filial,
                etapa=EtapaOrdem.Etapa.ENTREGA,
                status=EtapaOrdem.Status.CONCLUIDA,
                data_conclusao__gte=inicio,
                data_conclusao__lte=fim,
            )
            .values_list('ordem_id', flat=True)
        )
        ids = list(etapas_finais)
        if not ids:
            return []

        minutos_por_ordem = dict(
            EtapaOrdem.objects
            .filter(ordem_id__in=ids)
            .values_list('ordem_id')
            .annotate(total=Sum('tempo_minutos'))
        )
        ordens = (
            OrdemProducao.all_objects
            .filter(pk__in=ids)
            .select_related('item__produto__roteiro')
        )
        return [
            (o, minutos_por_ordem.get(o.pk) or ZERO) for o in ordens
        ]

    @staticmethod
    def _pedidos_abertos(filial):
        return list(
            PedidoProducao.objects.for_filial(filial)
            .exclude(status__in=PedidoProducao.STATUS_ENCERRADOS)
            .exclude(status=PedidoProducao.Status.ORCAMENTO)
            .select_related('cliente')
        )

    @staticmethod
    def _resumo_atraso(atrasados) -> str:
        if not atrasados:
            return 'nenhum pedido vencido'
        pior = min(p.dias_para_entrega for p in atrasados)
        return f'o mais antigo tem {-pior} dias de atraso'

    # O WIP devolve as colunas em LISTA, para preservar a ordem dos baldes
    # no painel. Este módulo lia como se fosse dicionário -- e derrubava o
    # dashboard inteiro com AttributeError, em toda visita. A chave de cada
    # coluna mora em `coluna.balde.chave`.
    @staticmethod
    def _pecas_do_balde(wip, chave) -> int:
        for coluna in wip['colunas']:
            if coluna.balde.chave == chave:
                return coluna.pecas
        return 0

    @staticmethod
    def _em_producao(wip) -> int:
        """Tudo que está no chão de fábrica menos o que já está pronto."""
        return sum(
            c.pecas for c in wip['colunas'] if c.balde.chave != 'prontas'
        )

    # ── Gráficos ─────────────────────────────────────────────────────────

    @classmethod
    def _graficos(cls, filial, pedidos, etapas, cortes, inicio, fim) -> list:
        return [
            cls._g_producao_por_dia(etapas, inicio, fim),
            cls._g_pedidos_por_status(filial),
            cls._g_producao_por_setor(etapas),
            cls._g_perdas_por_setor(etapas),
            cls._g_aproveitamento(cortes, inicio, fim),
            cls._g_entregas(filial, inicio, fim),
            cls._g_margem_por_produto(pedidos),
            cls._g_custo_por_colecao(filial, inicio, fim),
        ]

    @staticmethod
    def _g_producao_por_dia(etapas, inicio, fim) -> Grafico:
        por_dia = defaultdict(int)
        for e in etapas:
            por_dia[e.data_conclusao] += e.quantidade_produzida

        # Todos os dias do período, inclusive os zerados: buraco na série é
        # informação — dia sem produção some se só os dias com peça entram.
        fatias = []
        dia = inicio
        while dia <= fim:
            quantidade = por_dia.get(dia, 0)
            fatias.append(Fatia(f'{dia:%d/%m}', Decimal(quantidade),
                                f'{quantidade} peças'))
            dia += timedelta(days=1)

        return Grafico('producao_dia', 'Produção por dia',
                       'Peças concluídas em cada etapa, por data de conclusão.',
                       fatias, 'peças', tipo='barras')

    @staticmethod
    def _g_pedidos_por_status(filial) -> Grafico:
        """
        Todos os pedidos abertos por status — sem filtro de período.

        A pergunta é "onde está a carteira agora", e um pedido entrado há
        seis meses e ainda em produção é justamente o que precisa aparecer.
        """
        contagem = dict(
            PedidoProducao.objects.for_filial(filial)
            .exclude(status__in=PedidoProducao.STATUS_ENCERRADOS)
            .values_list('status')
            .annotate(total=Count('id'))
        )
        fatias = [
            Fatia(rotulo, Decimal(contagem.get(valor, 0)),
                  f'{contagem.get(valor, 0)} pedidos')
            for valor, rotulo in PedidoProducao.Status.choices
            if valor not in PedidoProducao.STATUS_ENCERRADOS
        ]
        return Grafico('pedidos_status', 'Pedidos por status',
                       'Carteira aberta agora, na ordem do fluxo.', fatias, 'pedidos')

    @staticmethod
    def _g_producao_por_setor(etapas) -> Grafico:
        por_etapa = defaultdict(int)
        for e in etapas:
            por_etapa[e.etapa] += e.quantidade_produzida

        fatias = [
            Fatia(rotulo, Decimal(por_etapa.get(valor, 0)),
                  f'{por_etapa.get(valor, 0)} peças')
            for valor, rotulo in EtapaOrdem.Etapa.choices
            if por_etapa.get(valor)
        ]
        return Grafico('producao_setor', 'Produção por setor',
                       'Peças concluídas no período, por etapa do fluxo.',
                       fatias, 'peças')

    @staticmethod
    def _g_perdas_por_setor(etapas) -> Grafico:
        perdas = defaultdict(int)
        produzido = defaultdict(int)
        for e in etapas:
            perdas[e.etapa] += e.perda
            produzido[e.etapa] += e.quantidade_produzida

        fatias = []
        for valor, rotulo in EtapaOrdem.Etapa.choices:
            if not perdas.get(valor):
                continue
            percentual = _pct(perdas[valor], produzido[valor] + perdas[valor])
            fatias.append(Fatia(
                rotulo, Decimal(perdas[valor]),
                f'{perdas[valor]} peças · {percentual:.1f}%'.replace('.', ','),
                tom='ruim' if percentual >= 5 else 'atencao',
            ))
        return Grafico('perdas_setor', 'Perdas por setor',
                       'Onde a peça se perde. O percentual é sobre o que passou '
                       'pela etapa.', fatias, 'peças')

    @staticmethod
    def _g_aproveitamento(cortes, inicio, fim) -> Grafico:
        por_dia = defaultdict(lambda: [ZERO, ZERO])
        for c in cortes:
            if c.aproveitamento_efetivo <= 0:
                continue
            peso = c.consumo_real or Decimal('1')
            por_dia[c.data][0] += c.aproveitamento_efetivo * peso
            por_dia[c.data][1] += peso

        fatias = []
        for dia in sorted(por_dia):
            soma, peso = por_dia[dia]
            media = (soma / peso).quantize(Decimal('0.1')) if peso else ZERO
            fatias.append(Fatia(
                f'{dia:%d/%m}', media, f'{media:.1f}%'.replace('.', ','),
                tom='bom' if media >= 85 else 'atencao' if media >= 75 else 'ruim',
            ))
        return Grafico('aproveitamento', 'Aproveitamento de tecido',
                       'Média ponderada por dia de corte. Abaixo de 75% o risco '
                       'precisa ser revisto.', fatias, '%', tipo='barras')

    @staticmethod
    def _g_entregas(filial, inicio, fim) -> Grafico:
        """
        Entregas do período: no prazo contra atrasadas.

        Conta pedidos ENTREGUES, comparando a entrega real com a combinada.
        Sem data de conclusão gravada no pedido, a referência é a última
        etapa de entrega concluída — que é onde o fluxo carimba a data.
        """
        entregues = list(
            PedidoProducao.objects.for_filial(filial)
            .filter(status=PedidoProducao.Status.ENTREGUE)
            .filter(data_prevista_entrega__gte=inicio, data_prevista_entrega__lte=fim)
        )
        no_prazo = atrasadas = 0
        for pedido in entregues:
            real = (
                EtapaOrdem.objects
                .filter(ordem__pedido=pedido, etapa=EtapaOrdem.Etapa.ENTREGA,
                        status=EtapaOrdem.Status.CONCLUIDA)
                .order_by('-data_conclusao')
                .values_list('data_conclusao', flat=True)
                .first()
            )
            if real is None or not pedido.data_prevista_entrega:
                continue
            if real <= pedido.data_prevista_entrega:
                no_prazo += 1
            else:
                atrasadas += 1

        total = no_prazo + atrasadas
        fatias = [
            Fatia('No prazo', Decimal(no_prazo),
                  f'{no_prazo} pedidos · {_pct(no_prazo, total):.0f}%', tom='bom'),
            Fatia('Atrasadas', Decimal(atrasadas),
                  f'{atrasadas} pedidos · {_pct(atrasadas, total):.0f}%', tom='ruim'),
        ]
        return Grafico('entregas', 'Entregas no prazo × atrasadas',
                       'Pedidos entregues cuja data combinada cai no período.',
                       fatias, 'pedidos')

    @staticmethod
    def _g_margem_por_produto(pedidos) -> Grafico:
        """
        Margem de cada produto vendido no período.

        Produto sem ficha ou sem roteiro não entra — margem sem custo daria
        100% e colocaria justamente o produto mal cadastrado no topo do
        gráfico.
        """
        receita = defaultdict(Decimal)
        custo = defaultdict(Decimal)
        nomes = {}

        for pedido in pedidos:
            for item in pedido.itens.all():
                produto = item.produto
                if produto is None:
                    continue
                ficha = getattr(produto, 'ficha', None)
                roteiro = getattr(produto, 'roteiro', None)
                if ficha is None and roteiro is None:
                    continue

                unitario = ZERO
                if ficha is not None:
                    unitario += ficha.custo_estimado
                if roteiro is not None:
                    unitario += roteiro.custo_total

                nomes[produto.pk] = produto.nome
                receita[produto.pk] += item.subtotal
                custo[produto.pk] += unitario * item.quantidade

        fatias = []
        for pk, valor in receita.items():
            if valor <= 0:
                continue
            margem = ((valor - custo[pk]) / valor * CEM).quantize(Decimal('0.1'))
            fatias.append(Fatia(
                nomes[pk], margem, f'{margem:.1f}%'.replace('.', ','),
                tom='ruim' if margem < 10 else 'bom' if margem >= 25 else 'atencao',
            ))
        fatias.sort(key=lambda f: f.valor, reverse=True)
        return Grafico('margem_produto', 'Margem por produto',
                       'Só produtos com ficha ou roteiro cadastrado — sem custo, '
                       'a margem seria 100%.', fatias[:12], '%')

    @staticmethod
    def _g_custo_por_colecao(filial, inicio, fim) -> Grafico:
        """Custo de produção das ordens do período, agrupado por coleção."""
        ordens = (
            OrdemProducao.objects.for_filial(filial)
            .filter(emitida_em__date__gte=inicio, emitida_em__date__lte=fim)
            .select_related(
                'item__produto__colecao', 'item__produto__ficha',
                'item__produto__roteiro',
            )
        )
        total = defaultdict(Decimal)
        for ordem in ordens:
            custo = ordem.custo_total
            if custo <= 0:
                continue
            produto = ordem.produto
            colecao = getattr(produto, 'colecao', None) if produto else None
            total[str(colecao) if colecao else 'Sem coleção'] += custo

        fatias = [
            Fatia(nome, valor, _dinheiro(valor))
            for nome, valor in sorted(total.items(), key=lambda x: -x[1])
        ]
        return Grafico('custo_colecao', 'Custo por coleção',
                       'Custo de produção das ordens emitidas no período.',
                       fatias[:12], 'R$')
