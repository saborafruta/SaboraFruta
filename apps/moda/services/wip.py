"""
WIP — quanto trabalho está no chão de fábrica e onde.

A conta sai do fluxo que já existe: cada ordem aberta está numa etapa (a
primeira não encerrada), e a quantidade planejada dessa etapa é o que ainda
está em processo ali.

UMA PEÇA APARECE NUM BALDE SÓ. Isso importa mais do que parece: "Cortadas"
e "Aguardando sublimação" são o MESMO conjunto físico — as peças que
saíram do corte e ainda não entraram na estampa. Listar as duas como baldes
separados somaria as mesmas peças duas vezes, e o total do painel deixaria
de bater com o que existe na fábrica. Aqui o balde é um só, com os dois
nomes, e o total fecha.

O WIP de uma etapa é a quantidade PLANEJADA dela, não o que falta produzir:
peça já produzida numa etapa que ainda não foi concluída continua no chão de
fábrica, não saiu para a seguinte. Medir o que falta responderia "quanto
falta fazer aqui", que é outra pergunta.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from apps.moda.models import EtapaOrdem, OrdemProducao

E = EtapaOrdem.Etapa
S = EtapaOrdem.Status


@dataclass(frozen=True)
class Balde:
    """Um estado do WIP: etapa + situação, com o nome que o chão usa."""
    chave: str
    label: str
    # Segundo nome do mesmo conjunto, quando existe. "Cortadas" e
    # "aguardando sublimação" são a mesma pilha vista de dois lados.
    tambem: str
    etapa: str
    status: tuple[str, ...]
    setor: str


# A ordem é a do fluxo. Cada balde é um par (etapa, status) e eles não se
# sobrepõem: uma ordem está numa etapa só, com um status só.
BALDES = [
    Balde('aguardando_corte', 'Aguardando corte', '', E.CORTE, (S.PENDENTE,), 'Corte'),
    Balde('em_corte', 'Em corte', '', E.CORTE, (S.EM_ANDAMENTO,), 'Corte'),
    Balde('cortadas', 'Cortadas', 'aguardando sublimação',
          E.ESTAMPA, (S.PENDENTE,), 'Estamparia'),
    Balde('em_estampa', 'Em sublimação / bordado / silk', '',
          E.ESTAMPA, (S.EM_ANDAMENTO,), 'Estamparia'),
    Balde('sublimadas', 'Sublimadas', 'aguardando costura',
          E.COSTURA, (S.PENDENTE,), 'Costura'),
    Balde('em_costura', 'Em costura', '', E.COSTURA, (S.EM_ANDAMENTO,), 'Costura'),
    Balde('acabamento', 'Em acabamento', '',
          E.ACABAMENTO, (S.PENDENTE, S.EM_ANDAMENTO), 'Acabamento'),
    Balde('qualidade', 'Em qualidade', '',
          E.QUALIDADE, (S.PENDENTE, S.EM_ANDAMENTO), 'Qualidade'),
    Balde('prontas', 'Prontas', 'embalagem, expedição e entrega',
          '', (), 'Expedição'),
]

BALDES_POR_ETAPA: dict[tuple[str, str], str] = {
    (b.etapa, s): b.chave for b in BALDES for s in b.status
}

# Etapas anteriores ao corte: o trabalho ainda é de escritório, não está no
# chão de fábrica. Ficam num balde à parte para não sumirem da conta.
ANTES_DO_CHAO = (E.PEDIDO, E.PLANEJAMENTO, E.MATERIAIS)
DEPOIS_DA_QUALIDADE = (E.EMBALAGEM, E.EXPEDICAO, E.ENTREGA)


@dataclass
class Linha:
    """Uma ordem no painel, com onde ela está e quanto tem parado ali."""
    ordem: OrdemProducao
    etapa: EtapaOrdem
    balde: str
    quantidade: int


@dataclass
class Coluna:
    balde: Balde
    linhas: list[Linha] = field(default_factory=list)

    @property
    def pecas(self) -> int:
        return sum(l.quantidade for l in self.linhas)

    @property
    def ordens(self) -> int:
        return len(self.linhas)


class WipService:

    # ── Consulta ─────────────────────────────────────────────────────────

    @staticmethod
    def base(filial):
        return (
            OrdemProducao.objects.for_filial(filial)
            .exclude(status__in=OrdemProducao.STATUS_ENCERRADOS)
            .select_related('pedido', 'pedido__cliente', 'item', 'item__produto')
            .prefetch_related('etapas')
        )

    @classmethod
    def painel(cls, filial, filtros: dict | None = None) -> dict:
        filtros = filtros or {}
        ordens = cls._filtrar(cls.base(filial), filtros)

        colunas = {b.chave: Coluna(balde=b) for b in BALDES}
        escritorio: list[Linha] = []
        sem_fluxo: list[OrdemProducao] = []

        for ordem in ordens:
            etapas = list(ordem.etapas.all())
            if not etapas:
                sem_fluxo.append(ordem)
                continue

            atual = next((e for e in etapas if not e.encerrada), None)
            if atual is None:
                # Fluxo inteiro encerrado com a ordem ainda aberta: as peças
                # existem e estão prontas, esperando alguém fechar a OP.
                atual = etapas[-1]
                chave = 'prontas'
            else:
                chave = cls._balde_de(atual)

            # Filtros que dependem da etapa atual só podem ser aplicados
            # aqui, depois de saber qual ela é.
            if not cls._passa_na_etapa(atual, filtros):
                continue

            linha = Linha(
                ordem=ordem, etapa=atual, balde=chave or '',
                quantidade=atual.planejada,
            )
            if chave is None:
                escritorio.append(linha)
            else:
                colunas[chave].linhas.append(linha)

        lista = [colunas[b.chave] for b in BALDES]
        return {
            'colunas': lista,
            'por_setor': cls._por_setor(lista),
            'escritorio': escritorio,
            'sem_fluxo': sem_fluxo,
            'total_pecas': sum(c.pecas for c in lista),
            'total_ordens': sum(c.ordens for c in lista),
        }

    @staticmethod
    def _balde_de(etapa: EtapaOrdem) -> str | None:
        """
        Em que balde esta etapa cai. None = ainda não chegou ao chão.

        Embalagem em diante é tudo "prontas": a peça já existe e passou pela
        qualidade; se está esperando caixa ou transporte, do ponto de vista
        da produção acabou.
        """
        if etapa.etapa in ANTES_DO_CHAO:
            return None
        if etapa.etapa in DEPOIS_DA_QUALIDADE:
            return 'prontas'
        return BALDES_POR_ETAPA.get((etapa.etapa, etapa.status))

    # ── Filtros ──────────────────────────────────────────────────────────

    @staticmethod
    def _filtrar(qs, filtros: dict):
        if filtros.get('ordem'):
            qs = qs.filter(numero__icontains=filtros['ordem'])
        if filtros.get('cliente'):
            qs = qs.filter(pedido__cliente__razao_social__icontains=filtros['cliente'])
        if filtros.get('produto'):
            qs = qs.filter(item__produto__nome__icontains=filtros['produto'])
        if filtros.get('de'):
            qs = qs.filter(prazo__gte=filtros['de'])
        if filtros.get('ate'):
            qs = qs.filter(prazo__lte=filtros['ate'])
        return qs

    @staticmethod
    def _passa_na_etapa(etapa: EtapaOrdem, filtros: dict) -> bool:
        """
        Filtros de setor e responsável.

        Ficam fora do queryset porque dependem de qual é a etapa ATUAL, e
        isso só se sabe percorrendo as etapas em Python: filtrar no banco
        traria ordens cujo responsável aparece em qualquer etapa, inclusive
        nas já concluídas — a pessoa deixaria de ver a própria fila e
        passaria a ver o próprio histórico.
        """
        setor = filtros.get('setor')
        if setor and setor != etapa.etapa:
            return False

        responsavel = (filtros.get('responsavel') or '').strip().lower()
        if responsavel and responsavel not in (etapa.responsavel or '').lower():
            return False

        return True

    # ── Agrupamento por setor ────────────────────────────────────────────

    @staticmethod
    def _por_setor(colunas: list[Coluna]) -> list[dict]:
        """
        A leitura do exemplo: Corte 500, Sublimação 320, Costura 180…

        Junta os baldes do mesmo setor. Como nenhuma peça está em dois
        baldes, a soma daqui é o WIP total — o número que se compara com o
        estoque de peças em processo.
        """
        totais: dict[str, dict] = {}
        for coluna in colunas:
            setor = coluna.balde.setor
            atual = totais.setdefault(setor, {'setor': setor, 'pecas': 0, 'ordens': 0})
            atual['pecas'] += coluna.pecas
            atual['ordens'] += coluna.ordens

        maior = max((t['pecas'] for t in totais.values()), default=0)
        linhas = list(totais.values())
        for linha in linhas:
            # Barra proporcional ao MAIOR setor, não ao total: com 8 setores
            # o percentual sobre o total daria barras minúsculas e o gráfico
            # não diria nada.
            linha['proporcao'] = round(linha['pecas'] / maior * 100, 1) if maior else 0
        return linhas

    # ── Opções dos filtros ───────────────────────────────────────────────

    @staticmethod
    def opcoes_setor() -> list[tuple[str, str]]:
        """Etapas que representam chão de fábrica, para o select de setor."""
        rotulos = dict(EtapaOrdem.Etapa.choices)
        return [
            (e, rotulos[e]) for e in (
                E.CORTE, E.ESTAMPA, E.COSTURA, E.ACABAMENTO,
                E.QUALIDADE, E.EMBALAGEM, E.EXPEDICAO,
            )
        ]

    @staticmethod
    def data(texto: str) -> date | None:
        """Converte a data do filtro; entrada inválida vira 'sem filtro'."""
        try:
            return datetime.strptime((texto or '').strip(), '%Y-%m-%d').date()
        except ValueError:
            return None
