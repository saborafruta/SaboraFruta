"""
Semiacabados — o estoque que está no chão de fábrica.

PEÇA EM PROCESSO É ESTOQUE, e é o estoque que ninguém vê. Ela não aparece em
falta nenhuma, não tem prateleira e não dispara alerta — mas já consumiu
tecido e horas de bancada, e esse dinheiro só volta quando a peça sai. Por
isso esta tela existe ao lado das de tecido, aviamento e produto: é a quarta
pilha de dinheiro parado, e a única que anda.

NÃO É O PAINEL DE WIP. Aquele (`Indicadores › WIP`) responde "onde o
trabalho está e o que travou"; este responde "quanto vale o que está lá e há
quanto tempo está parado". Mesma matéria-prima, perguntas diferentes — e a
contagem dos baldes vem de lá, importada, porque duas implementações do
mesmo agrupamento divergiriam e as duas telas passariam a discordar sobre
quantas peças a fábrica tem no chão.

PARAR TARDE CUSTA MAIS CARO. Uma peça encalhada depois da costura já pagou
tecido, corte e costura; a mesma peça encalhada antes do corte não pagou
quase nada. Por isso o valor não é peça × preço médio: cada balde carrega o
custo que a peça REALMENTE acumulou até ali, e é o que faz uma pilha pequena
no fim do fluxo pesar mais que uma pilha grande no começo.

ONDE O CUSTO ENTRA:

  MATERIAL entra no CORTE, de uma vez. É lá que o tecido deixa de ser rolo e
      vira peça, e ele é a maior parte do material de uma confecção. Os
      aviamentos entram junto, e não no ponto em que são pregados, porque
      ninguém aponta quando um zíper é usado — inventar esse ponto daria
      precisão falsa.
  MÃO DE OBRA entra setor a setor, conforme cada etapa é CONCLUÍDA. Etapa
      em andamento ainda não gerou crédito: a peça está em cima da bancada,
      não saiu dela.
"""
from decimal import Decimal

from django.utils import timezone

from ..models import EtapaOrdem
from .custo_real import mao_de_obra_por_setor
from .eficiencia import ETAPA_DO_SETOR
from .wip import WipService

ZERO = Decimal('0')
CENTAVO = Decimal('0.01')

# A partir daqui a pilha deixa de ser fluxo e vira encalhe. Três semanas é o
# que separa "está andando" de "alguém esqueceu": nenhuma etapa de confecção
# leva tanto tempo sozinha.
DIAS_ENCALHADO = 21

# Quantas ordens paradas listar. Mais que isto vira lista para rolar, e a
# tela deixa de responder "o que destravar hoje".
ANTIGAS = 8


class EstoqueSemiacabadoService:
    """Quanto vale o que está no chão, balde a balde."""

    @classmethod
    def painel(cls, filial) -> dict:
        wip = WipService.painel(filial)
        hoje = timezone.localdate()
        cache: dict[int, dict] = {}

        linhas = []
        paradas = []
        for coluna in wip['colunas']:
            linha = cls._balde(coluna, hoje, cache)
            if linha['pecas'] or linha['ordens']:
                linhas.append(linha)
            paradas.extend(linha.pop('ordens_detalhe'))

        # As mais paradas primeiro: a tela responde "o que destravar hoje", e
        # a resposta tem de estar no topo.
        paradas.sort(key=lambda o: -o['dias'])
        return {
            'linhas': linhas,
            'paradas': paradas[:ANTIGAS],
            'resumo': cls._resumo(linhas, paradas),
            'sem_custo': sum(1 for p in paradas if p['sem_custo']),
            'limite': DIAS_ENCALHADO,
        }

    # ── Um balde ─────────────────────────────────────────────────────────

    @classmethod
    def _balde(cls, coluna, hoje, cache) -> dict:
        pecas = 0
        valor = ZERO
        detalhe = []
        for l in coluna.linhas:
            unitario = cls._custo_acumulado(l.ordem, cache)
            dias = cls._dias_parada(l.ordem, l.etapa, hoje)
            pecas += l.quantidade
            valor += unitario * l.quantidade
            detalhe.append({
                'ordem': l.ordem,
                'numero': l.ordem.numero,
                'produto': l.ordem.item.nome_exibicao if l.ordem.item else '—',
                'cliente': (
                    l.ordem.pedido.cliente.razao_social
                    if l.ordem.pedido and l.ordem.pedido.cliente else '—'
                ),
                'balde': coluna.balde.label,
                'pecas': l.quantidade,
                'valor': (unitario * l.quantidade).quantize(CENTAVO),
                'dias': dias,
                'encalhada': dias >= DIAS_ENCALHADO,
                'sem_custo': unitario <= ZERO,
            })

        mais_antiga = max((d['dias'] for d in detalhe), default=None)
        return {
            'chave': coluna.balde.chave,
            'label': coluna.balde.label,
            'tambem': coluna.balde.tambem,
            'setor': coluna.balde.setor,
            'pecas': pecas,
            'ordens': len(coluna.linhas),
            'valor': valor.quantize(CENTAVO),
            # Por peça, e não só o total: é este número que mostra a peça
            # ficando mais cara conforme desce a fábrica.
            'por_peca': (valor / pecas).quantize(CENTAVO) if pecas else None,
            'dias': mais_antiga,
            'encalhado': mais_antiga is not None and mais_antiga >= DIAS_ENCALHADO,
            'ordens_detalhe': detalhe,
        }

    # ── Custo acumulado ──────────────────────────────────────────────────

    @classmethod
    def _custo_acumulado(cls, ordem, cache) -> Decimal:
        """
        Quanto UMA peça daquela ordem já consumiu até onde ela está.

        Memorizado por ordem: o mesmo cálculo se repetiria para cada linha
        do balde, e ele percorre ficha e roteiro inteiros.
        """
        if ordem.pk in cache:
            return cache[ordem.pk]

        concluidas = {
            e.etapa for e in ordem.etapas.all()
            if e.status == EtapaOrdem.Status.CONCLUIDA
        }
        total = ZERO

        # Material: entra de uma vez no corte. Antes disso o tecido ainda é
        # rolo, e contá-lo aqui somaria ao WIP algo que está na prateleira.
        ficha = ordem.ficha
        if ficha is not None and EtapaOrdem.Etapa.CORTE in concluidas:
            total += ficha.custo_estimado

        # Mão de obra: setor a setor, só o que já foi concluído.
        for setor, custos in mao_de_obra_por_setor(ordem.roteiro).items():
            etapa = ETAPA_DO_SETOR.get(setor)
            if etapa in concluidas:
                total += custos['por_peca'] + custos['por_hora']

        cache[ordem.pk] = total
        return total

    # ── Tempo parada ─────────────────────────────────────────────────────

    @staticmethod
    def _dias_parada(ordem, etapa, hoje) -> int:
        """
        Há quantos dias a pilha está onde está.

        Conta do início da etapa atual; sem ele, da conclusão da anterior;
        sem nenhuma das duas, da emissão da ordem. A cascata existe porque o
        apontamento de data é irregular no chão de fábrica, e devolver zero
        por falta de data faria a ordem mais esquecida parecer a mais nova.
        """
        if etapa.data_inicio:
            return max((hoje - etapa.data_inicio).days, 0)

        anteriores = [
            e.data_conclusao for e in ordem.etapas.all()
            if e.sequencia < etapa.sequencia
            and e.status == EtapaOrdem.Status.CONCLUIDA and e.data_conclusao
        ]
        if anteriores:
            return max((hoje - max(anteriores)).days, 0)
        return max((hoje - ordem.emitida_em.date()).days, 0)

    # ── Cabeçalho ────────────────────────────────────────────────────────

    @staticmethod
    def _resumo(linhas, paradas) -> dict:
        encalhadas = [p for p in paradas if p['encalhada']]
        return {
            'pecas': sum(l['pecas'] for l in linhas),
            'ordens': sum(l['ordens'] for l in linhas),
            'valor': sum((l['valor'] for l in linhas), ZERO).quantize(CENTAVO),
            'encalhadas': len(encalhadas),
            'valor_encalhado': sum(
                (p['valor'] for p in encalhadas), ZERO,
            ).quantize(CENTAVO),
            # Onde está mais DINHEIRO, e não mais peça: é a pilha pequena no
            # fim do fluxo que costuma pesar mais que a grande no começo.
            'mais_caro': max(
                (l for l in linhas if l['valor']), key=lambda l: l['valor'],
                default=None,
            ),
            # Calculada aqui em vez de assumir a ordem da lista: quem chamar
            # este resumo com as paradas em outra ordem receberia a errada.
            'mais_antiga': max(paradas, key=lambda p: p['dias'], default=None),
        }
