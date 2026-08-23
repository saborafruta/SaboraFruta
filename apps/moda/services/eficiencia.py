"""
Eficiência — o que a fábrica entregou contra o que ela podia entregar.

MINUTO É A MOEDA COMUM, e é o que torna a comparação honesta. Peça por hora
não serve de medida: quarenta camisas simples e quarenta conjuntos bordados
ocupam a costura de maneiras muito diferentes. O roteiro diz quantos minutos
cada peça consome em cada setor, e o cadastro de capacidade diz quantos
minutos o setor tem — as duas pontas na mesma unidade.

SÃO DUAS PERGUNTAS DIFERENTES, e a tela responde as duas porque confundi-las
é o erro clássico deste indicador:

  USO DA CAPACIDADE = minutos ganhos ÷ minutos disponíveis
      O setor entregou o que a capacidade dele prometia? Precisa só do
      roteiro, então funciona em fábrica que não cronometra nada.

  EFICIÊNCIA = minutos ganhos ÷ minutos apontados
      Enquanto esteve trabalhando, a bancada andou no ritmo do padrão?
      Precisa de alguém ter cronometrado, e por isso cobre menos.

Um setor pode estar a 130% de eficiência e a 40% de uso: a bancada corre,
mas passa a maior parte do dia parada esperando serviço. Só a eficiência
diria que está tudo ótimo — e o pedido atrasaria do mesmo jeito.

MINUTOS GANHOS são o padrão do roteiro vezes as peças BOAS produzidas: é o
crédito que a produção ganhou. Peça perdida não gera crédito nenhum, e é
por aí que a perda aparece na eficiência sem precisar de coluna própria.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from ..models import CapacidadeSetor, EtapaOrdem, Operacao

ZERO = Decimal('0')
CEM = Decimal('100')
DIAS_NA_SEMANA = Decimal('7')

# Setor de engenharia -> etapa do fluxo que o acompanha.
#
# O fluxo tem onze etapas fixas e o catálogo de operações tem sete setores;
# eles se encontram aqui, e num lugar só. MODELAGEM ficou de fora de
# propósito: não existe etapa que a acompanhe, então a capacidade dela
# apareceria eternamente ociosa por falta de apontamento, e não por estar
# parada de verdade.
ETAPA_DO_SETOR = {
    Operacao.Setor.CORTE: EtapaOrdem.Etapa.CORTE,
    Operacao.Setor.ESTAMPARIA: EtapaOrdem.Etapa.ESTAMPA,
    Operacao.Setor.COSTURA: EtapaOrdem.Etapa.COSTURA,
    Operacao.Setor.ACABAMENTO: EtapaOrdem.Etapa.ACABAMENTO,
    Operacao.Setor.QUALIDADE: EtapaOrdem.Etapa.QUALIDADE,
    Operacao.Setor.EXPEDICAO: EtapaOrdem.Etapa.EXPEDICAO,
}
SETOR_DA_ETAPA = {etapa: setor for setor, etapa in ETAPA_DO_SETOR.items()}

PERIODOS = (('7', '7 dias'), ('30', '30 dias'), ('90', '90 dias'))


def minutos_por_setor(roteiro) -> dict[str, Decimal]:
    """
    Quantos minutos UMA peça consome em cada setor, segundo o roteiro.

    O roteiro é por OPERAÇÃO (quinze linhas) e a capacidade é por SETOR
    (sete). Somar as operações dentro do setor é o que põe os dois na mesma
    régua — sem isso, comparar o tempo de "pregar gola" com a capacidade da
    costura inteira daria um número sem significado.
    """
    minutos: dict[str, Decimal] = {}
    if roteiro is None:
        return minutos
    for etapa in roteiro.etapas.all():
        setor = etapa.operacao.setor
        minutos[setor] = minutos.get(setor, ZERO) + (etapa.tempo or ZERO)
    return minutos


def _pct(numerador, denominador):
    """Percentual, ou None quando não há base — que NÃO é o mesmo que 0%."""
    if not denominador:
        return None
    return (Decimal(numerador) / Decimal(denominador) * CEM).quantize(Decimal('0.1'))


class EficienciaService:
    """Minutos ganhos, apontados e disponíveis, setor a setor."""

    @classmethod
    def painel(cls, filial, dias: int) -> dict:
        desde = timezone.localdate() - timedelta(days=dias)
        etapas = cls._etapas(filial, desde)
        capacidades = {
            c.setor: c for c in CapacidadeSetor.objects.for_filial(filial)
        }

        linhas = cls._linhas(etapas, capacidades, dias)
        return {
            'linhas': linhas,
            'desde': desde,
            'resumo': cls._resumo(linhas),
            'sem_capacidade': not capacidades,
        }

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def _etapas(filial, desde):
        """
        Etapas concluídas no período, com o roteiro do produto junto.

        O `prefetch` das operações existe porque o cálculo lê o tempo de
        cada uma: sem ele seria uma consulta por etapa, e noventa dias de
        fábrica são muitas etapas.
        """
        return list(
            EtapaOrdem.objects
            .filter(
                ordem__filial=filial,
                etapa__in=SETOR_DA_ETAPA,
                status=EtapaOrdem.Status.CONCLUIDA,
                data_conclusao__gte=desde,
                quantidade_produzida__gt=0,
            )
            .select_related('ordem__item__produto__roteiro')
            .prefetch_related('ordem__item__produto__roteiro__etapas__operacao')
        )

    # ── Conta ────────────────────────────────────────────────────────────

    @classmethod
    def _linhas(cls, etapas, capacidades, dias) -> list[dict]:
        acumulado: dict[str, dict] = {}
        cache: dict[int, dict] = {}

        for etapa in etapas:
            setor = SETOR_DA_ETAPA[etapa.etapa]
            linha = acumulado.setdefault(setor, cls._linha_vazia(setor))
            produzido = etapa.quantidade_produzida
            linha['pecas'] += produzido
            linha['ordens'].add(etapa.ordem_id)

            padrao = cls._padrao(etapa.ordem.roteiro, setor, cache)
            if not padrao:
                # Sem roteiro — ou sem operação daquele setor nele — não há
                # padrão com que comparar. Contar como zero derrubaria o
                # indicador de todo mundo por falta de cadastro, então a
                # peça fica de fora da conta e aparece na cobertura.
                continue

            ganho = padrao * produzido
            linha['ganho'] += ganho
            linha['pecas_com_padrao'] += produzido

            # A eficiência só pode somar minuto ganho onde HÁ minuto
            # apontado: incluir etapa sem cronômetro mandaria a razão para
            # o infinito.
            if etapa.tempo_minutos and etapa.tempo_minutos > 0:
                linha['ganho_medido'] += ganho
                linha['apontado'] += etapa.tempo_minutos
                linha['pecas_medidas'] += produzido

        return cls._fechar(acumulado, capacidades, dias)

    @staticmethod
    def _linha_vazia(setor) -> dict:
        return {
            'setor': setor,
            'label': Operacao.Setor(setor).label,
            'pecas': 0,
            'pecas_com_padrao': 0,
            'pecas_medidas': 0,
            'ordens': set(),
            'ganho': ZERO,
            'ganho_medido': ZERO,
            'apontado': ZERO,
        }

    @staticmethod
    def _padrao(roteiro, setor, cache) -> Decimal:
        """Minutos/peça daquele setor no roteiro, memorizado por roteiro."""
        if roteiro is None:
            return ZERO
        if roteiro.pk not in cache:
            cache[roteiro.pk] = minutos_por_setor(roteiro)
        return cache[roteiro.pk].get(setor, ZERO)

    @classmethod
    def _fechar(cls, acumulado, capacidades, dias) -> list[dict]:
        """Fecha as razões e devolve NA ORDEM DO FLUXO, nunca por valor."""
        linhas = []
        for setor in ETAPA_DO_SETOR:
            capacidade = capacidades.get(setor)
            linha = acumulado.get(setor)
            # Setor sem capacidade cadastrada E sem produção não é linha:
            # seria uma fileira de traços vazios em toda fábrica que usa
            # metade dos setores.
            if linha is None and capacidade is None:
                continue
            linha = linha or cls._linha_vazia(setor)

            disponivel = cls._disponivel(capacidade, dias)
            linha['ordens'] = len(linha['ordens'])
            linha['disponivel'] = disponivel
            linha['tem_capacidade'] = capacidade is not None
            linha['uso'] = _pct(linha['ganho'], disponivel)
            linha['eficiencia'] = _pct(linha['ganho_medido'], linha['apontado'])
            linha['cobertura'] = _pct(linha['pecas_com_padrao'], linha['pecas'])
            linha['sem_padrao'] = linha['pecas'] - linha['pecas_com_padrao']
            linha['ocioso'] = max(disponivel - linha['ganho'], ZERO)
            # A barra para em 100%, mas `estourou` guarda que passou: o
            # setor entregou mais do que a capacidade cadastrada, e isso é
            # hora extra ou cadastro desatualizado. Nos dois casos precisa
            # aparecer, em vez de a barra sair da tela.
            linha['barra'] = min(int(linha['uso'] or 0), 100)
            linha['estourou'] = (linha['uso'] or ZERO) > CEM
            linhas.append(linha)
        return linhas

    @staticmethod
    def _disponivel(capacidade, dias) -> Decimal:
        """
        Minutos que o setor teve no período.

        Os dias úteis saem da proporção da semana cadastrada (cinco de
        sete, seis de sete) e não de um calendário de verdade. É uma
        aproximação assumida: feriado e ponto facultativo não estão em
        lugar nenhum do sistema, e inventar um calendário aqui daria uma
        precisão falsa.
        """
        if capacidade is None:
            return ZERO
        uteis = Decimal(dias) * Decimal(capacidade.dias_semana) / DIAS_NA_SEMANA
        return (capacidade.minutos_dia * uteis).quantize(Decimal('0.01'))

    # ── Cabeçalho ────────────────────────────────────────────────────────

    @staticmethod
    def _resumo(linhas) -> dict:
        ganho = sum((l['ganho'] for l in linhas), ZERO)
        disponivel = sum((l['disponivel'] for l in linhas), ZERO)
        ganho_medido = sum((l['ganho_medido'] for l in linhas), ZERO)
        apontado = sum((l['apontado'] for l in linhas), ZERO)

        # O setor mais APERTADO manda no prazo: é ele que satura primeiro e
        # segura a fábrica inteira, por mais folga que os outros tenham.
        com_uso = [l for l in linhas if l['uso'] is not None]
        return {
            'uso': _pct(ganho, disponivel),
            'eficiencia': _pct(ganho_medido, apontado),
            'ganho': ganho.quantize(Decimal('0.1')),
            'disponivel': disponivel.quantize(Decimal('0.1')),
            'ocioso': max(disponivel - ganho, ZERO).quantize(Decimal('0.1')),
            'apertado': max(com_uso, key=lambda l: l['uso'], default=None),
            'pecas': sum(l['pecas'] for l in linhas),
            'sem_padrao': sum(l['sem_padrao'] for l in linhas),
            'sem_apontamento': not apontado,
        }
