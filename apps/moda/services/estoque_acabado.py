"""
Acabados — a peça pronta que ainda não saiu.

O CUSTO JÁ FOI TODO PAGO E A RECEITA AINDA NÃO ENTROU. Este é o estoque mais
caro da fábrica: cada peça aqui já consumiu tecido, aviamento e todas as
bancadas, e só vira dinheiro quando chega ao cliente. Um dia parado aqui é
um dia de capital preso no ponto máximo.

A RÉGUA NÃO É A IDADE, É O PRAZO. Semiacabados olha há quanto tempo a pilha
está parada, porque no meio do fluxo não há data prometida. Aqui há: o
pedido tem `data_prevista_entrega`, e uma peça pronta há dois dias com o
prazo vencido é pior que uma pronta há vinte com o prazo em três semanas.
Por isso a fila ordena por ATRASO primeiro, e só depois por tempo parado.

O ACHADO DESTA TELA é a ordem PRONTA SEM EXPEDIÇÃO ABERTA. A peça terminou,
ninguém abriu o documento, e ela some de todas as filas: não está mais na
produção e ainda não entrou na expedição. É o buraco entre os dois módulos,
e é onde a peça fica esquecida mais tempo.

DESPACHADO NÃO ENTRA NO TOTAL. A carga já saiu do galpão — continua sem
aceite do cliente, e por isso aparece num cartão à parte, mas somá-la ao
"pronto esperando" diria que há peça na prateleira que não está mais lá.

"Pronta" segue a MESMA regra que o botão de abrir expedição usa: a etapa de
Qualidade encerrada (concluída ou pulada), ou ausente no fluxo. Duas
definições fariam a tela listar ordem que o botão recusa.
"""
from decimal import Decimal

from django.utils import timezone

from ..models import EtapaOrdem, Expedicao, OrdemProducao

ZERO = Decimal('0')
CENTAVO = Decimal('0.01')

S = Expedicao.Status

# A fila de saída, na ordem do processo. `sem_expedicao` vem primeiro
# porque é o pior lugar para uma peça estar: fora das duas filas.
FILA = (
    ('sem_expedicao', 'Sem expedição aberta'),
    (S.PRODUCAO_CONCLUIDA.value, 'Produção concluída'),
    (S.CONFERENCIA.value, 'Conferência'),
    (S.SEPARACAO.value, 'Separação'),
    (S.EMBALAGEM.value, 'Embalagem'),
)
ROTULOS = dict(FILA)

# Status que ainda ocupam o galpão. Despacho fica de fora: a carga saiu.
EM_CASA = {chave for chave, _ in FILA}


class EstoqueAcabadoService:
    """O que está pronto, quanto vale e se já passou do prazo."""

    @classmethod
    def painel(cls, filial) -> dict:
        hoje = timezone.localdate()
        expedicoes = cls._expedicoes(filial)

        # TODA expedição não cancelada tira a ordem da busca por "sem
        # documento", inclusive a já ENTREGUE -- senão a ordem entregue
        # voltaria para a prateleira, porque ela não tem expedição VIVA.
        com_documento = set(
            Expedicao.objects.for_filial(filial)
            .exclude(status=S.CANCELADA)
            .values_list('ordem_id', flat=True)
        )

        linhas = [cls._da_expedicao(e, hoje) for e in expedicoes]
        linhas += [
            cls._da_ordem(o, hoje)
            for o in cls._prontas_sem_expedicao(filial, com_documento)
        ]

        em_casa = [l for l in linhas if l['chave'] in EM_CASA]
        a_caminho = [l for l in linhas if l['chave'] == S.DESPACHO.value]

        # ATRASADAS primeiro, e dentro delas o maior atraso no topo; depois
        # as demais pelo tempo parado. É a ordem em que a expedição deve
        # pegar as caixas.
        em_casa.sort(key=lambda l: (
            not l['atrasada'], -(l['dias_atraso'] or 0), -l['dias_pronta'],
        ))
        return {
            'linhas': em_casa,
            'a_caminho': a_caminho,
            'por_fila': cls._por_fila(em_casa),
            'resumo': cls._resumo(em_casa, a_caminho),
        }

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def _expedicoes(filial):
        """Expedições vivas: nem entregues, nem canceladas."""
        return list(
            Expedicao.objects.for_filial(filial)
            .exclude(status__in=(S.ENTREGA, S.CANCELADA))
            .select_related('ordem__pedido__cliente', 'ordem__item__produto__ficha',
                            'ordem__item__produto__roteiro')
            .prefetch_related('ordem__etapas',
                              'ordem__item__produto__ficha__materiais',
                              'ordem__item__produto__roteiro__etapas__operacao')
        )

    @classmethod
    def _prontas_sem_expedicao(cls, filial, com_documento):
        """
        Ordens que terminaram a produção e ninguém abriu o documento.

        O buraco entre produção e expedição: a peça não está mais numa fila
        nem entrou na outra, e é onde ela fica esquecida mais tempo.
        """
        ordens = (
            OrdemProducao.objects.for_filial(filial)
            .exclude(status=OrdemProducao.Status.CANCELADA)
            .exclude(pk__in=com_documento)
            .select_related('pedido__cliente', 'item__produto__ficha',
                            'item__produto__roteiro')
            .prefetch_related('etapas', 'item__produto__ficha__materiais',
                              'item__produto__roteiro__etapas__operacao')
        )
        return [o for o in ordens if cls._producao_terminou(o)]

    @staticmethod
    def _producao_terminou(ordem) -> bool:
        """
        Mesma regra do botão de abrir expedição: Qualidade encerrada, ou
        ausente do fluxo. Duas definições fariam a tela listar ordem que o
        botão recusa.
        """
        etapas = list(ordem.etapas.all())
        if not etapas:
            return False
        qualidade = next(
            (e for e in etapas if e.etapa == EtapaOrdem.Etapa.QUALIDADE), None,
        )
        if qualidade is not None:
            return qualidade.encerrada
        # Sem etapa de qualidade, vale o fluxo produtivo inteiro encerrado.
        return all(e.encerrada for e in etapas)

    # ── Uma linha ────────────────────────────────────────────────────────

    @classmethod
    def _da_expedicao(cls, expedicao, hoje) -> dict:
        ordem = expedicao.ordem
        linha = cls._base(ordem, hoje)
        linha.update({
            'expedicao': expedicao,
            'chave': expedicao.status,
            'onde': ROTULOS.get(expedicao.status, expedicao.get_status_display()),
            'pecas': expedicao.quantidade_esperada,
            # A partir da abertura do documento, o relógio do "pronto" é o
            # dele: é a data em que alguém declarou a peça terminada.
            'pronta_desde': timezone.localtime(expedicao.criado_em).date(),
        })
        return cls._fechar(linha, hoje)

    @classmethod
    def _da_ordem(cls, ordem, hoje) -> dict:
        linha = cls._base(ordem, hoje)
        linha.update({
            'expedicao': None,
            'chave': 'sem_expedicao',
            'onde': ROTULOS['sem_expedicao'],
            'pecas': cls._pecas_prontas(ordem),
            'pronta_desde': cls._fim_da_producao(ordem),
        })
        return cls._fechar(linha, hoje)

    @staticmethod
    def _base(ordem, hoje) -> dict:
        pedido = ordem.pedido
        prazo = pedido.data_prevista_entrega if pedido else None
        return {
            'ordem': ordem,
            'numero': ordem.numero,
            'produto': ordem.item.nome_exibicao if ordem.item else '—',
            'cliente': (
                pedido.cliente.razao_social if pedido and pedido.cliente else '—'
            ),
            'pedido': pedido,
            'prazo': prazo,
            'atrasada': bool(prazo and prazo < hoje),
            'dias_atraso': (hoje - prazo).days if prazo and prazo < hoje else None,
        }

    @classmethod
    def _fechar(cls, linha, hoje) -> dict:
        pronta = linha['pronta_desde']
        linha['dias_pronta'] = max((hoje - pronta).days, 0) if pronta else 0
        unitario = cls._custo_unitario(linha['ordem'])
        linha['unitario'] = unitario
        linha['valor'] = (unitario * linha['pecas']).quantize(CENTAVO)
        linha['sem_custo'] = unitario <= ZERO
        return linha

    @staticmethod
    def _pecas_prontas(ordem) -> int:
        """
        O que a última bancada entregou — não a quantidade emitida.

        Entre a emissão e o fim morreu o que morreu, e contar a emitida
        colocaria na prateleira peça que virou refugo.
        """
        concluidas = [
            e for e in ordem.etapas.all()
            if e.status == EtapaOrdem.Status.CONCLUIDA and e.quantidade_produzida
        ]
        if not concluidas:
            return ordem.quantidade
        return max(concluidas, key=lambda e: e.sequencia).quantidade_produzida

    @staticmethod
    def _fim_da_producao(ordem):
        """Quando a última etapa concluída fechou."""
        datas = [
            e.data_conclusao for e in ordem.etapas.all()
            if e.status == EtapaOrdem.Status.CONCLUIDA and e.data_conclusao
        ]
        if datas:
            return max(datas)
        return timezone.localtime(ordem.emitida_em).date()

    @staticmethod
    def _custo_unitario(ordem) -> Decimal:
        """
        Custo cheio de UMA peça pronta: material da ficha + roteiro inteiro.

        Aqui não há absorção parcial como nos semiacabados — a peça passou
        por tudo, então carrega tudo.
        """
        ficha = ordem.ficha
        roteiro = ordem.roteiro
        total = ZERO
        if ficha is not None:
            total += ficha.custo_estimado
        if roteiro is not None:
            total += roteiro.custo_total
        return total

    # ── Agrupamentos ─────────────────────────────────────────────────────

    @staticmethod
    def _por_fila(linhas) -> list[dict]:
        """Uma linha por posição da fila, na ordem do processo."""
        totais = {
            chave: {'chave': chave, 'label': label, 'pecas': 0,
                    'ordens': 0, 'valor': ZERO, 'atrasadas': 0}
            for chave, label in FILA
        }
        for l in linhas:
            atual = totais[l['chave']]
            atual['pecas'] += l['pecas']
            atual['ordens'] += 1
            atual['valor'] += l['valor']
            atual['atrasadas'] += 1 if l['atrasada'] else 0
        return [
            {**t, 'valor': t['valor'].quantize(CENTAVO)}
            for t in totais.values() if t['ordens']
        ]

    @staticmethod
    def _resumo(em_casa, a_caminho) -> dict:
        atrasadas = [l for l in em_casa if l['atrasada']]
        sem_documento = [l for l in em_casa if l['chave'] == 'sem_expedicao']
        return {
            'pecas': sum(l['pecas'] for l in em_casa),
            'ordens': len(em_casa),
            'valor': sum((l['valor'] for l in em_casa), ZERO).quantize(CENTAVO),
            'atrasadas': len(atrasadas),
            'valor_atrasado': sum(
                (l['valor'] for l in atrasadas), ZERO,
            ).quantize(CENTAVO),
            # A pior é a de MAIOR ATRASO, e não a de maior valor: aqui o que
            # está em jogo é o cliente esperando, e o relógio dele não muda
            # com o tamanho do pedido.
            'pior': max(
                atrasadas, key=lambda l: l['dias_atraso'], default=None,
            ),
            'sem_documento': len(sem_documento),
            'pecas_sem_documento': sum(l['pecas'] for l in sem_documento),
            'a_caminho': len(a_caminho),
            'pecas_a_caminho': sum(l['pecas'] for l in a_caminho),
            'valor_a_caminho': sum(
                (l['valor'] for l in a_caminho), ZERO,
            ).quantize(CENTAVO),
            'sem_custo': sum(1 for l in em_casa if l['sem_custo']),
        }
