"""
PCP — capacidade disponível contra carga planejada.

A carga sai de três coisas que já existem: o pedido diz QUANTAS peças e PARA
QUANDO, o roteiro do produto diz por QUAIS setores elas passam e quantos
minutos consomem em cada um. Multiplicando e agrupando por semana sai a
carga. A capacidade vem de `CapacidadeSetor`.

Duas honestidades que o serviço carrega de propósito:

  - **Pedido sem roteiro não some.** Item cujo produto não tem roteiro (ou
    que nem tem produto de catálogo) não gera carga nenhuma — e um plano que
    esconde isso mostra a fábrica folgada quando ela não está. Esses itens
    voltam numa lista à parte, para a tela dizer de quanto o plano está
    incompleto.
  - **Atrasado consome capacidade agora.** Pedido com entrega vencida entra
    na semana corrente, não na semana que passou: o trabalho continua para
    ser feito, e jogá-lo no passado tiraria da conta justamente a carga que
    mais aperta.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.moda.models import CapacidadeSetor, Operacao, PedidoProducao

MINUTOS_POR_HORA = Decimal('60')

# Peso de cada prioridade na fila. Número e não ordem alfabética porque
# 'urgente' < 'normal' no alfabeto, e a fila sairia invertida.
PESO_PRIORIDADE = {
    PedidoProducao.Prioridade.URGENTE: 0,
    PedidoProducao.Prioridade.ALTA: 1,
    PedidoProducao.Prioridade.NORMAL: 2,
}


@dataclass
class Celula:
    """Carga de um setor numa semana."""
    semana: date
    minutos: Decimal = Decimal('0')
    capacidade: Decimal = Decimal('0')

    @property
    def percentual(self) -> Decimal:
        if not self.capacidade:
            return Decimal('0')
        return (self.minutos / self.capacidade * 100).quantize(Decimal('0.1'))

    @property
    def sobrecarga(self) -> bool:
        return bool(self.capacidade) and self.minutos > self.capacidade

    @property
    def horas(self) -> Decimal:
        return (self.minutos / MINUTOS_POR_HORA).quantize(Decimal('0.1'))


@dataclass
class LinhaSetor:
    setor: str
    label: str
    capacidade: Decimal
    celulas: list[Celula] = field(default_factory=list)

    @property
    def total_minutos(self) -> Decimal:
        return sum((c.minutos for c in self.celulas), Decimal('0'))

    @property
    def sem_capacidade(self) -> bool:
        """Tem carga mas ninguém cadastrou quanto o setor aguenta."""
        return not self.capacidade and self.total_minutos > 0


@dataclass
class ItemSemRoteiro:
    pedido: PedidoProducao
    descricao: str
    quantidade: int
    motivo: str


def semana_de(dia: date) -> date:
    """A segunda-feira da semana daquele dia — a chave dos buckets."""
    return dia - timedelta(days=dia.weekday())


class PcpService:

    # ── Consulta base ────────────────────────────────────────────────────

    @staticmethod
    def pedidos_abertos(filial):
        """
        Pedidos que ainda dão trabalho.

        `Entregue` e `Cancelado` ficam fora: não consomem capacidade e
        inflariam a carga de todas as semanas passadas.
        """
        return (
            PedidoProducao.objects.for_filial(filial)
            .exclude(status__in=PedidoProducao.STATUS_ENCERRADOS)
            .select_related('cliente')
            .prefetch_related(
                'itens__produto__roteiro__etapas__operacao',
            )
        )

    # ── Carga ────────────────────────────────────────────────────────────

    @classmethod
    def carga(cls, filial, semanas: int = 8, hoje: date | None = None) -> dict:
        hoje = hoje or timezone.localdate()
        primeira = semana_de(hoje)
        janela = [primeira + timedelta(weeks=i) for i in range(semanas)]
        ultima = janela[-1]

        capacidades = {
            c.setor: c.minutos_semana
            for c in CapacidadeSetor.objects.for_filial(filial)
        }

        # (setor, semana) -> minutos ; e (maquina) -> minutos
        por_setor: dict[tuple[str, date], Decimal] = defaultdict(Decimal)
        por_maquina: dict[str, Decimal] = defaultdict(Decimal)
        sem_roteiro: list[ItemSemRoteiro] = []
        sem_data: list[PedidoProducao] = []
        fora_do_horizonte = Decimal('0')

        for pedido in cls.pedidos_abertos(filial):
            if not pedido.data_prevista_entrega:
                sem_data.append(pedido)
                continue

            semana = semana_de(pedido.data_prevista_entrega)
            # Atrasado entra na semana corrente: o trabalho continua vivo.
            if semana < primeira:
                semana = primeira

            for item in pedido.itens.all():
                etapas = cls._etapas_do_item(item)
                if etapas is None:
                    sem_roteiro.append(ItemSemRoteiro(
                        pedido=pedido,
                        descricao=item.nome_exibicao,
                        quantidade=item.quantidade,
                        motivo='sem produto de catálogo' if not item.produto_id else 'produto sem roteiro',
                    ))
                    continue

                for etapa in etapas:
                    minutos = etapa.tempo * item.quantidade
                    if not minutos:
                        continue
                    if semana > ultima:
                        fora_do_horizonte += minutos
                        continue
                    por_setor[(etapa.operacao.setor, semana)] += minutos
                    por_maquina[etapa.maquina_efetiva or '— não informada —'] += minutos

        rotulos = dict(Operacao.Setor.choices)
        setores_com_algo = {s for s, _sem in por_setor} | set(capacidades)
        linhas = [
            LinhaSetor(
                setor=setor,
                label=rotulos.get(setor, setor),
                capacidade=capacidades.get(setor, Decimal('0')),
                celulas=[
                    Celula(
                        semana=semana,
                        minutos=por_setor.get((setor, semana), Decimal('0')),
                        capacidade=capacidades.get(setor, Decimal('0')),
                    )
                    for semana in janela
                ],
            )
            # Ordem do fluxo produtivo, não alfabética: é como a fábrica pensa.
            for setor in Operacao.Setor.values
            if setor in setores_com_algo
        ]

        total_maquinas = sum(por_maquina.values(), Decimal('0'))
        maquinas = sorted(
            (
                {
                    'maquina': nome,
                    'minutos': minutos,
                    'horas': (minutos / MINUTOS_POR_HORA).quantize(Decimal('0.1')),
                    'percentual': (
                        (minutos / total_maquinas * 100).quantize(Decimal('0.1'))
                        if total_maquinas else Decimal('0')
                    ),
                }
                for nome, minutos in por_maquina.items()
            ),
            key=lambda m: m['minutos'], reverse=True,
        )

        return {
            'semanas': janela,
            'linhas': linhas,
            'alertas': cls._alertas(linhas, sem_roteiro, sem_data, fora_do_horizonte),
            'maquinas': maquinas,
            'sem_roteiro': sem_roteiro,
            'sem_data': sem_data,
            'fora_do_horizonte': fora_do_horizonte,
        }

    @staticmethod
    def _etapas_do_item(item):
        """
        Etapas do roteiro do produto do item, ou None quando não há roteiro.

        None e lista vazia são coisas diferentes: vazia é um roteiro criado
        sem etapas (carga zero, e é isso mesmo); None é a falta do roteiro,
        que a tela precisa denunciar.
        """
        if not item.produto_id:
            return None
        roteiro = getattr(item.produto, 'roteiro', None)
        if roteiro is None:
            return None
        return list(roteiro.etapas.all())

    @staticmethod
    def _alertas(linhas, sem_roteiro, sem_data, fora_do_horizonte) -> list[dict]:
        alertas = []

        for linha in linhas:
            estouradas = [c for c in linha.celulas if c.sobrecarga]
            if estouradas:
                pior = max(estouradas, key=lambda c: c.percentual)
                alertas.append({
                    'nivel': 'sobrecarga',
                    'setor': linha.label,
                    'texto': (
                        f'{linha.label}: {len(estouradas)} semana(s) acima da capacidade. '
                        f'A pior é a de {pior.semana:%d/%m}, com {pior.percentual}% '
                        f'({pior.horas}h de carga contra {(pior.capacidade / MINUTOS_POR_HORA):.1f}h disponíveis).'
                    ),
                })
            elif linha.sem_capacidade:
                alertas.append({
                    'nivel': 'sem_capacidade',
                    'setor': linha.label,
                    'texto': (
                        f'{linha.label} tem carga planejada mas não tem capacidade '
                        f'cadastrada — não dá para saber se cabe.'
                    ),
                })

        if sem_roteiro:
            pecas = sum(i.quantidade for i in sem_roteiro)
            alertas.append({
                'nivel': 'incompleto',
                'setor': '',
                'texto': (
                    f'{len(sem_roteiro)} item(ns) de pedido, somando {pecas} peça(s), '
                    f'estão fora desta conta por não terem roteiro. A carga real é maior.'
                ),
            })

        if sem_data:
            alertas.append({
                'nivel': 'incompleto',
                'setor': '',
                'texto': (
                    f'{len(sem_data)} pedido(s) sem data prevista de entrega não '
                    f'entraram no plano — sem data não há semana onde alocá-los.'
                ),
            })

        if fora_do_horizonte:
            horas = (fora_do_horizonte / MINUTOS_POR_HORA).quantize(Decimal('0.1'))
            alertas.append({
                'nivel': 'info',
                'setor': '',
                'texto': f'{horas}h de carga caem depois do horizonte mostrado.',
            })

        return alertas

    # ── Fila / sequenciamento ────────────────────────────────────────────

    @classmethod
    def fila(cls, filial, hoje: date | None = None) -> list[dict]:
        """
        Pedidos na ordem em que devem entrar na fábrica.

        Prioridade primeiro, depois a data de entrega. Nesta ordem e não na
        inversa porque "urgente" existe justamente para furar a fila da data
        — se a data mandasse primeiro, marcar urgente não mudaria nada.

        Pedido sem data vai para o fim: não dá para prometer o que não tem
        prazo, e colocá-lo no meio empurraria pedidos com data combinada.
        """
        hoje = hoje or timezone.localdate()
        distante = date.max

        pedidos = list(cls.pedidos_abertos(filial))
        pedidos.sort(key=lambda p: (
            PESO_PRIORIDADE.get(p.prioridade, 9),
            p.data_prevista_entrega or distante,
            p.numero,
        ))

        acumulado = Decimal('0')
        linhas = []
        for posicao, pedido in enumerate(pedidos, start=1):
            minutos = cls.minutos_do_pedido(pedido)
            acumulado += minutos
            linhas.append({
                'posicao': posicao,
                'pedido': pedido,
                'minutos': minutos,
                'horas': (minutos / MINUTOS_POR_HORA).quantize(Decimal('0.1')),
                'acumulado_horas': (acumulado / MINUTOS_POR_HORA).quantize(Decimal('0.1')),
                'pecas': sum(i.quantidade for i in pedido.itens.all()),
                'dias': pedido.dias_para_entrega,
                'atrasado': pedido.atrasado,
            })
        return linhas

    @classmethod
    def minutos_do_pedido(cls, pedido) -> Decimal:
        """Minutos de fábrica que o pedido inteiro consome."""
        total = Decimal('0')
        for item in pedido.itens.all():
            etapas = cls._etapas_do_item(item)
            if etapas is None:
                continue
            total += sum((e.tempo for e in etapas), Decimal('0')) * item.quantidade
        return total

    # ── Programação ──────────────────────────────────────────────────────

    @classmethod
    def programacao(cls, filial, semanas: int = 8, hoje: date | None = None) -> list[dict]:
        """Os pedidos distribuídos pelas semanas do horizonte."""
        hoje = hoje or timezone.localdate()
        primeira = semana_de(hoje)
        janela = [primeira + timedelta(weeks=i) for i in range(semanas)]

        baldes: dict[date, list] = {s: [] for s in janela}
        adiante: list = []
        sem_data: list = []

        for pedido in cls.pedidos_abertos(filial):
            if not pedido.data_prevista_entrega:
                sem_data.append(pedido)
                continue
            # Atrasado sobe para a semana corrente, pelo mesmo motivo da
            # carga: o trabalho continua para ser feito.
            semana = max(semana_de(pedido.data_prevista_entrega), primeira)
            if semana in baldes:
                baldes[semana].append(pedido)
            else:
                adiante.append(pedido)

        linhas = []
        for semana in janela:
            pedidos = sorted(
                baldes[semana],
                key=lambda p: (PESO_PRIORIDADE.get(p.prioridade, 9),
                               p.data_prevista_entrega or date.max),
            )
            minutos = sum((cls.minutos_do_pedido(p) for p in pedidos), Decimal('0'))
            linhas.append({
                'semana': semana,
                'fim': semana + timedelta(days=6),
                'pedidos': pedidos,
                'pecas': sum(sum(i.quantidade for i in p.itens.all()) for p in pedidos),
                'horas': (minutos / MINUTOS_POR_HORA).quantize(Decimal('0.1')),
                'corrente': semana == primeira,
            })

        # Linha final com o que não coube no horizonte e o que não tem data.
        # Sem ela `adiante` e `sem_data` seriam listas mortas, e a tela
        # mostraria menos pedidos do que existem sem dizer que omitiu.
        resto = adiante + sem_data
        if resto:
            minutos = sum((cls.minutos_do_pedido(p) for p in resto), Decimal('0'))
            linhas.append({
                'semana': None,
                'fim': None,
                'pedidos': sorted(resto, key=lambda p: (p.data_prevista_entrega or date.max, p.numero)),
                'pecas': sum(sum(i.quantidade for i in p.itens.all()) for p in resto),
                'horas': (minutos / MINUTOS_POR_HORA).quantize(Decimal('0.1')),
                'corrente': False,
            })

        return linhas

    # ── Acompanhamento ───────────────────────────────────────────────────

    @classmethod
    def acompanhamento(cls, filial) -> list[dict]:
        """Quantos pedidos e peças estão em cada status, na ordem do fluxo."""
        pedidos = list(cls.pedidos_abertos(filial))
        por_status: dict[str, list] = defaultdict(list)
        for pedido in pedidos:
            por_status[pedido.status].append(pedido)

        rotulos = dict(PedidoProducao.Status.choices)
        return [
            {
                'status': status,
                'label': rotulos[status],
                'pedidos': sorted(
                    por_status[status],
                    key=lambda p: (p.data_prevista_entrega or date.max, p.numero),
                ),
                'quantidade': len(por_status[status]),
                'pecas': sum(
                    sum(i.quantidade for i in p.itens.all()) for p in por_status[status]
                ),
                'atrasados': sum(1 for p in por_status[status] if p.atrasado),
            }
            # Ordem do fluxo (a de `Status.choices`), e não por volume: a
            # tela é lida como uma esteira, da esquerda para a direita.
            for status in PedidoProducao.Status.values
            if status in por_status
        ]
