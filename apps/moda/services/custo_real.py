"""
Custo real contra o da ficha técnica.

A ficha diz quanto a peça DEVERIA custar. Esta tela diz quanto ela custou —
e a diferença entre os dois é onde o lucro do pedido foi parar.

O QUE É MEDIDO E O QUE NÃO É, dito na cara em vez de escondido:

  TECIDO tem medida real — `RegistroCorte.consumo_real` são os metros que
      saíram do rolo. O preço por metro vem da ficha, porque é lá que ele
      está; a quantidade vem da mesa de corte.
  MÃO DE OBRA tem medida real onde alguém cronometrou a etapa.
  AVIAMENTO não tem medida real em lugar nenhum do sistema: não existe
      apontamento de quantos zíperes foram usados. Entra pelo previsto, e a
      tela DIZ que entrou pelo previsto.

Carregar o não medido pelo previsto é a única saída honesta — o outro
caminho seria contá-lo como zero, e aí toda ordem apareceria barata.
Mas um "custo real" que silencia o que não mediu é pior do que não ter
custo nenhum: quem fecha preço em cima dele fecha com prejuízo e só
descobre no fim do mês. Por isso cada linha carrega a bandeira do que foi
estimado.

SÓ ORDEM CONCLUÍDA ENTRA. Ordem no meio da produção tem custo real parcial
contra previsto inteiro, e apareceria como uma economia enorme que não
existe — é o jeito mais fácil de esta tela mentir.

DUAS PERGUNTAS QUE PARECEM UMA SÓ. O custo TOTAL da ordem estourar é uma
coisa; o custo POR PEÇA estourar é outra. Uma ordem que perdeu peças pode
fechar dentro do orçamento total e ainda assim ter saído cara por peça, e é
o custo por peça que decide se o preço de venda estava certo. Por isso o
real por peça divide pelas peças BOAS: refugo não vira mercadoria, mas o
dinheiro dele já foi gasto.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Max, Q
from django.utils import timezone

from ..models import EtapaOrdem, MaterialFicha, OrdemProducao, RegistroCorte
from .eficiencia import ETAPA_DO_SETOR, SETOR_DA_ETAPA

ZERO = Decimal('0')
CEM = Decimal('100')
CENTAVO = Decimal('0.01')

PERIODOS = (('30', '30 dias'), ('90', '90 dias'), ('180', '180 dias'))


def _pct(numerador, denominador):
    """Percentual, ou None quando não há base — que NÃO é o mesmo que 0%."""
    if not denominador:
        return None
    return (Decimal(numerador) / Decimal(denominador) * CEM).quantize(Decimal('0.1'))


def preco_do_metro(ficha) -> Decimal:
    """
    Preço médio do metro de tecido principal, ponderado pelo consumo.

    Ponderado porque uma peça pode levar dois tecidos principais em
    proporções diferentes: a média simples de um forro caro usado em 10 cm
    com a malha barata usada em 1,20 m daria um preço que não existe.
    """
    if ficha is None:
        return ZERO
    principais = [
        m for m in ficha.materiais.all()
        if m.tipo == MaterialFicha.Tipo.TECIDO_PRINCIPAL
    ]
    metros = sum((m.consumo_bruto for m in principais), ZERO)
    if not metros:
        return ZERO
    total = sum((m.consumo_bruto * (m.custo_unitario or ZERO) for m in principais), ZERO)
    return (total / metros).quantize(Decimal('0.0001'))


def custo_do_tecido(ficha) -> Decimal:
    """Quanto o tecido principal custa em UMA peça, segundo a ficha."""
    if ficha is None:
        return ZERO
    return sum(
        (m.custo_total for m in ficha.materiais.all()
         if m.tipo == MaterialFicha.Tipo.TECIDO_PRINCIPAL),
        ZERO,
    )


def mao_de_obra_por_setor(roteiro) -> dict[str, dict]:
    """
    Custo e tempo padrão de cada setor, separando hora de peça.

    A separação importa: costura interna se paga por HORA, e demorar o dobro
    custa o dobro. Facção se paga por PEÇA, e demorar o dobro custa o mesmo.
    Tratar as duas iguais erraria o custo real numa direção ou na outra.
    """
    setores: dict[str, dict] = {}
    if roteiro is None:
        return setores
    for etapa in roteiro.etapas.all():
        operacao = etapa.operacao
        setor = setores.setdefault(operacao.setor, {
            'por_peca': ZERO,     # facção: custo fixo por peça produzida
            'por_hora': ZERO,     # custo/peça das operações pagas por hora
            'tempo_hora': ZERO,   # tempo padrão dessas operações
        })
        if operacao.tipo_custo == operacao.TipoCusto.POR_PECA:
            setor['por_peca'] += etapa.custo_peca
        else:
            setor['por_hora'] += etapa.custo_peca
            setor['tempo_hora'] += etapa.tempo or ZERO
    return setores


class CustoRealService:
    """Previsto × real, ordem a ordem."""

    @classmethod
    def painel(cls, filial, dias: int) -> dict:
        desde = timezone.localdate() - timedelta(days=dias)
        ordens = cls._ordens(filial, desde)
        linhas = [cls.da_ordem(o) for o in ordens]
        # Da maior variação para a menor: é a fila de quem investigar
        # primeiro, e o topo é onde o dinheiro sumiu.
        linhas.sort(key=lambda l: l['variacao'], reverse=True)
        return {
            'desde': desde,
            'linhas': linhas,
            'resumo': cls._resumo(linhas),
        }

    @staticmethod
    def _ordens(filial, desde):
        """
        Ordens CONCLUÍDAS que terminaram no período.

        A janela sai da última etapa concluída, porque a ordem não tem data
        de conclusão própria. E o status é exigido de propósito: ordem no
        meio do caminho compara custo real parcial com previsto inteiro, e
        apareceria como uma economia que não existe.
        """
        return list(
            OrdemProducao.objects.for_filial(filial)
            .filter(status=OrdemProducao.Status.CONCLUIDA)
            .annotate(fim=Max(
                'etapas__data_conclusao',
                filter=Q(etapas__status=EtapaOrdem.Status.CONCLUIDA),
            ))
            .filter(fim__gte=desde)
            .select_related('pedido__cliente', 'item__produto__ficha',
                            'item__produto__roteiro')
            .prefetch_related('etapas', 'cortes',
                              'item__produto__ficha__materiais',
                              'item__produto__roteiro__etapas__operacao')
        )

    # ── Uma ordem ────────────────────────────────────────────────────────

    @classmethod
    def da_ordem(cls, ordem) -> dict:
        ficha = ordem.ficha
        roteiro = ordem.roteiro
        material = cls._material(ordem, ficha)
        obra = cls._mao_de_obra(ordem, roteiro)

        previsto = material['previsto'] + obra['previsto']
        real = material['real'] + obra['real']
        boas = cls._pecas_boas(ordem)

        return {
            'ordem': ordem,
            'numero': ordem.numero,
            'produto': ordem.item.nome_exibicao if ordem.item else '—',
            'cliente': (
                ordem.pedido.cliente.razao_social
                if ordem.pedido and ordem.pedido.cliente else '—'
            ),
            'fim': ordem.fim,
            'quantidade': ordem.quantidade,
            'boas': boas,
            'material': material,
            'obra': obra,
            'previsto': previsto.quantize(CENTAVO),
            'real': real.quantize(CENTAVO),
            'variacao': (real - previsto).quantize(CENTAVO),
            'variacao_pct': _pct(real - previsto, previsto),
            'estourou': real > previsto,
            # Por PEÇA BOA: refugo não vira mercadoria, mas o dinheiro dele
            # já foi gasto — e é o custo por peça que diz se o preço de
            # venda estava certo.
            'unitario_previsto': (
                (previsto / ordem.quantidade).quantize(CENTAVO)
                if ordem.quantidade else ZERO
            ),
            'unitario_real': (real / boas).quantize(CENTAVO) if boas else None,
            'sem_ficha': ficha is None,
            'sem_roteiro': roteiro is None,
            # Uma linha é "estimada" quando qualquer pedaço do real veio do
            # previsto. Sem essa bandeira, um número montado com metade de
            # palpite passaria por medição.
            'estimado': material['estimado'] or obra['estimado'],
        }

    @staticmethod
    def _pecas_boas(ordem) -> int:
        """
        As peças que a última bancada entregou.

        Não é `ordem.quantidade`: entre a emissão e a entrega morreu o que
        morreu, e dividir o custo pela quantidade emitida esconderia
        exatamente o efeito do refugo no custo unitário.
        """
        concluidas = [
            e for e in ordem.etapas.all()
            if e.status == EtapaOrdem.Status.CONCLUIDA
            and e.etapa in SETOR_DA_ETAPA
        ]
        if not concluidas:
            return ordem.quantidade
        return max(concluidas, key=lambda e: e.sequencia).quantidade_produzida

    # ── Material ─────────────────────────────────────────────────────────

    @staticmethod
    def _material(ordem, ficha) -> dict:
        """
        Tecido pelo consumo real; o resto pelo previsto, e declarado.

        Não há apontamento de aviamento em lugar nenhum do sistema. Contar
        zíper e linha como zero deixaria toda ordem barata; carregá-los pelo
        previsto é o que sobra, e a bandeira `estimado` é o que impede que
        isso vire uma mentira silenciosa.
        """
        previsto = ordem.custo_materiais
        tecido_previsto = (custo_do_tecido(ficha) * ordem.quantidade).quantize(CENTAVO)
        outros = previsto - tecido_previsto

        metros = sum(
            ((c.consumo_real or ZERO) for c in ordem.cortes.all()
             if c.status == RegistroCorte.Status.CORTADO),
            ZERO,
        )
        preco = preco_do_metro(ficha)
        medido = bool(metros and preco)
        tecido_real = (metros * preco).quantize(CENTAVO) if medido else tecido_previsto

        return {
            'previsto': previsto,
            'real': (tecido_real + outros).quantize(CENTAVO),
            'tecido_previsto': tecido_previsto,
            'tecido_real': tecido_real,
            'tecido_variacao': (tecido_real - tecido_previsto).quantize(CENTAVO),
            'outros': outros,
            'metros': metros.quantize(Decimal('0.01')),
            'preco_metro': preco,
            'medido': medido,
            # Aviamento nunca é medido, então material só deixa de ser
            # estimado quando não há aviamento nenhum na ficha.
            'estimado': not medido or outros > ZERO,
        }

    # ── Mão de obra ──────────────────────────────────────────────────────

    @classmethod
    def _mao_de_obra(cls, ordem, roteiro) -> dict:
        """
        Só os setores que o FLUXO acompanha, dos dois lados da comparação.

        O roteiro pode ter operações de setor sem etapa correspondente —
        modelagem é o caso. Deixá-las só no previsto criaria uma economia
        permanente que nada no chão de fábrica produziu.
        """
        setores = mao_de_obra_por_setor(roteiro)
        etapas = {
            e.etapa: e for e in ordem.etapas.all()
            if e.status == EtapaOrdem.Status.CONCLUIDA and e.etapa in SETOR_DA_ETAPA
        }

        previsto = ZERO
        real = ZERO
        fora = ZERO
        estimado = False
        for setor, custos in setores.items():
            padrao_peca = custos['por_peca'] + custos['por_hora']
            etapa = etapas.get(ETAPA_DO_SETOR.get(setor))
            if etapa is None:
                # Setor sem etapa no fluxo (modelagem) ou etapa não
                # concluída: fica fora dos DOIS lados, e é declarado.
                fora += padrao_peca * ordem.quantidade
                continue

            previsto += padrao_peca * ordem.quantidade
            produzidas = etapa.quantidade_produzida
            # Facção: paga por peça, e demorar o dobro custa o mesmo.
            real += custos['por_peca'] * produzidas

            if etapa.tempo_minutos and custos['tempo_hora'] > 0:
                # Hora: a taxa por minuto sai do próprio roteiro, e o tempo
                # apontado é o que manda.
                taxa = custos['por_hora'] / custos['tempo_hora']
                real += etapa.tempo_minutos * taxa
            else:
                real += custos['por_hora'] * produzidas
                if custos['por_hora'] > 0:
                    estimado = True

        return {
            'previsto': previsto.quantize(CENTAVO),
            'real': real.quantize(CENTAVO),
            'variacao': (real - previsto).quantize(CENTAVO),
            'fora_do_fluxo': fora.quantize(CENTAVO),
            'estimado': estimado,
        }

    # ── Cabeçalho ────────────────────────────────────────────────────────

    @staticmethod
    def _resumo(linhas) -> dict:
        previsto = sum((l['previsto'] for l in linhas), ZERO)
        real = sum((l['real'] for l in linhas), ZERO)
        estouraram = [l for l in linhas if l['estourou']]
        return {
            'ordens': len(linhas),
            'previsto': previsto.quantize(CENTAVO),
            'real': real.quantize(CENTAVO),
            'variacao': (real - previsto).quantize(CENTAVO),
            'variacao_pct': _pct(real - previsto, previsto),
            'estouraram': len(estouraram),
            # A pior ordem é a de maior variação em REAIS, e não em
            # percentual: aqui a pergunta é onde o dinheiro sumiu, e 5% de
            # uma ordem de mil peças pesa mais que 50% de uma de dez.
            'pior': max(linhas, key=lambda l: l['variacao'], default=None),
            'tecido': sum((l['material']['tecido_variacao'] for l in linhas), ZERO).quantize(CENTAVO),
            'obra': sum((l['obra']['variacao'] for l in linhas), ZERO).quantize(CENTAVO),
            'estimadas': sum(1 for l in linhas if l['estimado']),
            'sem_ficha': sum(1 for l in linhas if l['sem_ficha']),
        }
