"""
Prazos — o que chegou na data combinada, e o que ainda vai chegar tarde.

DUAS PERGUNTAS, E SÓ UMA DELAS DÁ PARA CONSERTAR. O placar do que já foi
entregue é histórico: serve para saber se a fábrica cumpre o que promete, e
não há mais nada a fazer sobre ele. A lista do que está aberto é a de hoje —
o pedido que vai atrasar ainda pode ser puxado na frente. Por isso as duas
vivem na mesma tela e em blocos separados.

ANTECIPAÇÃO NÃO COMPENSA ATRASO. Entregar dez dias adiantado para um cliente
não desfaz dez dias de atraso para outro, e uma média com sinal daria zero
numa fábrica que deixou os dois insatisfeitos. Aqui o placar é CONTAGEM (no
prazo × atrasados) e o atraso médio é calculado SÓ SOBRE OS ATRASADOS.

A MÉDIA ESCONDE A CAUDA, e é por isso que existe a distribuição. "Atraso
médio de cinco dias" pode ser uma fábrica que atrasa cinco dias sempre — ou
uma que entrega quase tudo em dia e destruiu dois clientes com trinta dias.
São situações opostas e pedem providências opostas.

PEDIDO SEM DATA COMBINADA NÃO ENTRA NO PLACAR. Não se pode estar atrasado
contra uma data que não existe; contá-lo como "no prazo" inflaria o
indicador com pedido que ninguém prometeu. Ele fica de fora e é declarado —
é falha de cadastro, e é onde o atraso se esconde.

A JANELA É PELA ENTREGA REAL: "do que saiu no período, quanto chegou no
prazo". O dashboard olha pela data prometida ("do que prometi para o mês"),
que é outra pergunta legítima — e por isso os dois números podem diferir.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from ..models import EtapaOrdem, Expedicao, PedidoProducao

ZERO = Decimal('0')
CEM = Decimal('100')

PERIODOS = (('30', '30 dias'), ('90', '90 dias'), ('180', '180 dias'))

# Os baldes do atraso. O primeiro corte é em 3 dias porque uma semana de
# confecção tem esse tanto de folga informal; o último é aberto porque
# atraso de mês não tem teto e não pode ser diluído com o de três dias.
FAIXAS = (
    ('no_prazo', 'No prazo', None, 0),
    ('ate_3', 'Até 3 dias', 1, 3),
    ('ate_7', '4 a 7 dias', 4, 7),
    ('ate_15', '8 a 15 dias', 8, 15),
    ('acima_15', 'Mais de 15 dias', 16, None),
)

# Quantos dias à frente contam como "vence logo". É o horizonte em que
# ainda dá para puxar um pedido na frente sem virar madrugada.
JANELA_RISCO = 7


def _pct(numerador, denominador):
    """Percentual, ou None quando não há base — que NÃO é o mesmo que 0%."""
    if not denominador:
        return None
    return (Decimal(numerador) / Decimal(denominador) * CEM).quantize(Decimal('0.1'))


def faixa_do_atraso(dias: int) -> str:
    for chave, _, minimo, maximo in FAIXAS:
        if minimo is None:
            if dias <= 0:
                return chave
            continue
        if dias >= minimo and (maximo is None or dias <= maximo):
            return chave
    return 'acima_15'


class PrazoService:
    """O placar do que foi entregue e o risco do que está aberto."""

    @classmethod
    def painel(cls, filial, dias: int) -> dict:
        hoje = timezone.localdate()
        desde = hoje - timedelta(days=dias)

        entregues = cls._entregues(filial, desde, hoje)
        abertos = cls._abertos(filial, hoje)
        return {
            'desde': desde,
            'entregues': entregues['linhas'],
            'placar': entregues['placar'],
            'faixas': entregues['faixas'],
            'abertos': abertos['linhas'],
            'risco': abertos['resumo'],
            'janela': JANELA_RISCO,
        }

    # ── O que já foi entregue ────────────────────────────────────────────

    @classmethod
    def _entregues(cls, filial, desde, hoje) -> dict:
        pedidos = (
            PedidoProducao.objects.for_filial(filial)
            .filter(status=PedidoProducao.Status.ENTREGUE)
            .select_related('cliente')
            .prefetch_related('ordens__expedicoes', 'ordens__etapas')
        )

        linhas = []
        sem_prazo = 0
        sem_data = 0
        for pedido in pedidos:
            real = cls._data_de_entrega(pedido)
            if real is None or real < desde or real > hoje:
                if real is None:
                    # Marcado como entregue e sem carimbo de quando: não dá
                    # para dizer se chegou no prazo, e chutar seria pior.
                    sem_data += 1
                continue
            prometido = pedido.data_prevista_entrega
            if prometido is None:
                sem_prazo += 1
                continue
            desvio = (real - prometido).days
            linhas.append({
                'pedido': pedido,
                'numero': pedido.numero,
                'cliente': pedido.cliente.razao_social if pedido.cliente_id else '—',
                'prometido': prometido,
                'real': real,
                'desvio': desvio,
                'atrasado': desvio > 0,
                'adiantado': -desvio if desvio < 0 else 0,
                'faixa': faixa_do_atraso(desvio),
            })

        # Do maior atraso para o menor: o placar é resumo, e a lista é para
        # olhar o caso — o caso que interessa é o pior.
        linhas.sort(key=lambda l: -l['desvio'])
        return {
            'linhas': linhas,
            'placar': cls._placar(linhas, sem_prazo, sem_data),
            'faixas': cls._faixas(linhas),
        }

    @staticmethod
    def _data_de_entrega(pedido):
        """
        Quando o pedido chegou de verdade — a ÚLTIMA carga a chegar.

        Prefere o aceite do cliente na expedição, que é assinatura de quem
        recebeu; sem ele vale a etapa de Entrega do fluxo, que é o carimbo
        de quem despachou. Um pedido com várias ordens só está entregue
        quando a última chegou, por isso o máximo.
        """
        datas = []
        for ordem in pedido.ordens.all():
            for expedicao in ordem.expedicoes.all():
                if expedicao.status == Expedicao.Status.ENTREGA and expedicao.data_entrega:
                    datas.append(timezone.localtime(expedicao.data_entrega).date())
            for etapa in ordem.etapas.all():
                if (etapa.etapa == EtapaOrdem.Etapa.ENTREGA
                        and etapa.status == EtapaOrdem.Status.CONCLUIDA
                        and etapa.data_conclusao):
                    datas.append(etapa.data_conclusao)
        return max(datas) if datas else None

    @staticmethod
    def _placar(linhas, sem_prazo, sem_data) -> dict:
        atrasados = [l for l in linhas if l['atrasado']]
        total = len(linhas)
        return {
            'entregues': total,
            'no_prazo': total - len(atrasados),
            'atrasados': len(atrasados),
            'otd': _pct(total - len(atrasados), total),
            # SÓ sobre os atrasados: incluir os que chegaram em dia dividiria
            # o atraso por quem não atrasou e faria trinta dias virar três.
            'atraso_medio': (
                round(sum(l['desvio'] for l in atrasados) / len(atrasados), 1)
                if atrasados else None
            ),
            'pior': max(atrasados, key=lambda l: l['desvio'], default=None),
            'sem_prazo': sem_prazo,
            'sem_data': sem_data,
        }

    @staticmethod
    def _faixas(linhas) -> list[dict]:
        """
        A distribuição existe porque a média esconde a cauda: entregar quase
        tudo em dia e destruir dois clientes com trinta dias dá a mesma
        média de atrasar pouco em tudo, e pede providência oposta.
        """
        contagem = {chave: 0 for chave, _, _, _ in FAIXAS}
        for linha in linhas:
            contagem[linha['faixa']] += 1
        maior = max(contagem.values(), default=0)
        total = len(linhas)
        return [
            {
                'chave': chave,
                'label': label,
                'pedidos': contagem[chave],
                'percentual': _pct(contagem[chave], total),
                # Barra contra a MAIOR faixa, não contra o total: com cinco
                # faixas o percentual sobre o total daria barras minúsculas.
                'barra': int(contagem[chave] / maior * 100) if maior else 0,
                'boa': chave == 'no_prazo',
            }
            for chave, label, _, _ in FAIXAS
        ]

    # ── O que ainda está aberto ──────────────────────────────────────────

    @classmethod
    def _abertos(cls, filial, hoje) -> dict:
        pedidos = (
            PedidoProducao.objects.for_filial(filial)
            .exclude(status__in=PedidoProducao.STATUS_ENCERRADOS)
            .exclude(status=PedidoProducao.Status.ORCAMENTO)
            .select_related('cliente')
        )

        linhas = []
        sem_prazo = 0
        for pedido in pedidos:
            prazo = pedido.data_prevista_entrega
            if prazo is None:
                # Sem data combinada não há risco a calcular -- e é aqui
                # que o atraso costuma se esconder.
                sem_prazo += 1
                linhas.append(cls._aberto(pedido, None))
                continue
            linhas.append(cls._aberto(pedido, (prazo - hoje).days))

        # Os mais urgentes no topo; sem prazo por último, porque ali não há
        # urgência calculável, e sim cadastro faltando.
        linhas.sort(key=lambda l: (l['dias'] is None, l['dias'] or 0))
        atrasados = [l for l in linhas if l['atrasado']]
        return {
            'linhas': linhas,
            'resumo': {
                'abertos': len(linhas),
                'atrasados': len(atrasados),
                'vencem': sum(1 for l in linhas if l['vence_logo']),
                'sem_prazo': sem_prazo,
                'pior': min(atrasados, key=lambda l: l['dias'], default=None),
            },
        }

    @staticmethod
    def _aberto(pedido, dias) -> dict:
        return {
            'pedido': pedido,
            'numero': pedido.numero,
            'cliente': pedido.cliente.razao_social if pedido.cliente_id else '—',
            'status': pedido.get_status_display(),
            'prazo': pedido.data_prevista_entrega,
            'dias': dias,
            'atrasado': dias is not None and dias < 0,
            'vence_logo': dias is not None and 0 <= dias <= JANELA_RISCO,
            # Dias de atraso em positivo: o template nao deve precisar tirar
            # o sinal de uma string para mostrar "3 dias atras".
            'atraso': -dias if dias is not None and dias < 0 else 0,
        }
