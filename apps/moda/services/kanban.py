"""
Kanban de produção — as ordens como cartões, arrastáveis entre colunas.

O quadro não guarda estado próprio: a coluna de um cartão é derivada da
etapa em que a ordem está, no mesmo fluxo que o painel e o WIP leem. Guardar
uma "coluna" no banco criaria uma segunda verdade sobre onde a ordem está, e
no primeiro apontamento feito pelo fluxo as duas divergiriam.

ARRASTAR É UM APONTAMENTO, não uma mudança cosmética. Soltar um cartão em
Costura afirma que a ordem chegou lá — logo, que corte e estampa acabaram.
O serviço conclui as etapas puladas e DEVOLVE quais foram, para a tela
dizer: concluir três etapas de uma vez é uma decisão, e uma decisão tomada
sem aviso vira dado inventado no relatório de produção.

Arrastar para trás reabre as etapas seguintes, mas NÃO apaga o que foi
apontado nelas: quantidade produzida e perda ficam. Voltar um cartão é
quase sempre correção de status, não desfazimento de produção — e apagar
números que alguém digitou seria o tipo de perda silenciosa que faz o
usuário parar de confiar na tela.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.moda.models import EtapaOrdem, OrdemProducao

E = EtapaOrdem.Etapa
S = EtapaOrdem.Status
StatusOrdem = OrdemProducao.Status


@dataclass(frozen=True)
class Coluna:
    chave: str
    label: str
    # Etapas que caem nesta coluna.
    etapas: tuple[str, ...]
    # Etapa que passa a valer quando um cartão é solto aqui.
    destino: str


COLUNAS = [
    Coluna('planejamento', 'Planejamento',
           (E.PEDIDO, E.PLANEJAMENTO, E.MATERIAIS), E.PLANEJAMENTO),
    Coluna('corte', 'Corte', (E.CORTE,), E.CORTE),
    Coluna('sublimacao', 'Sublimação', (E.ESTAMPA,), E.ESTAMPA),
    Coluna('costura', 'Costura', (E.COSTURA,), E.COSTURA),
    Coluna('acabamento', 'Acabamento', (E.ACABAMENTO,), E.ACABAMENTO),
    Coluna('qualidade', 'Qualidade', (E.QUALIDADE,), E.QUALIDADE),
    Coluna('embalagem', 'Embalagem', (E.EMBALAGEM,), E.EMBALAGEM),
    Coluna('expedicao', 'Expedição', (E.EXPEDICAO,), E.EXPEDICAO),
    Coluna('concluido', 'Concluído', (E.ENTREGA,), E.ENTREGA),
]

COLUNAS_POR_CHAVE = {c.chave: c for c in COLUNAS}
COLUNA_DA_ETAPA = {e: c.chave for c in COLUNAS for e in c.etapas}
ORDEM_DAS_ETAPAS = {e.value: i for i, e in enumerate(E)}


@dataclass
class Cartao:
    ordem: OrdemProducao
    etapa: EtapaOrdem
    percentual: object
    coluna: str


@dataclass
class Raia:
    coluna: Coluna
    cartoes: list[Cartao] = field(default_factory=list)

    @property
    def pecas(self) -> int:
        return sum(c.ordem.quantidade for c in self.cartoes)

    @property
    def total(self) -> int:
        return len(self.cartoes)


class KanbanService:

    # ── Montagem do quadro ───────────────────────────────────────────────

    @staticmethod
    def base(filial):
        return (
            OrdemProducao.objects.for_filial(filial)
            .exclude(status=StatusOrdem.CANCELADA)
            .select_related('pedido', 'pedido__cliente', 'item', 'item__produto')
            .prefetch_related('etapas')
        )

    @classmethod
    def quadro(cls, filial, filtros: dict | None = None) -> dict:
        from apps.moda.services.fluxo import FluxoService

        filtros = filtros or {}
        ordens = cls._filtrar(cls.base(filial), filtros)

        raias = {c.chave: Raia(coluna=c) for c in COLUNAS}
        sem_fluxo = []

        for ordem in ordens:
            etapas = list(ordem.etapas.all())
            if not etapas:
                sem_fluxo.append(ordem)
                continue

            resumo = FluxoService.resumo(ordem)
            atual = resumo['atual']

            if atual is None or ordem.status == StatusOrdem.CONCLUIDA:
                # Fluxo terminado (ou OP fechada): o cartão descansa no fim.
                chave, atual = 'concluido', atual or etapas[-1]
            else:
                chave = COLUNA_DA_ETAPA.get(atual.etapa, 'planejamento')

            raias[chave].cartoes.append(Cartao(
                ordem=ordem, etapa=atual,
                percentual=resumo['percentual'], coluna=chave,
            ))

        # Dentro da raia, o mais urgente primeiro: prioridade e depois prazo.
        # É a mesma ordem da fila do PCP — o quadro não pode sugerir uma
        # sequência diferente da que o planejamento definiu.
        from apps.moda.services.pcp import PESO_PRIORIDADE
        from datetime import date

        for raia in raias.values():
            raia.cartoes.sort(key=lambda c: (
                PESO_PRIORIDADE.get(c.ordem.prioridade, 9),
                c.ordem.prazo or date.max,
                c.ordem.numero,
            ))

        return {
            'raias': [raias[c.chave] for c in COLUNAS],
            'sem_fluxo': sem_fluxo,
            'total': sum(r.total for r in raias.values()),
        }

    @staticmethod
    def _filtrar(qs, filtros: dict):
        if filtros.get('cliente'):
            qs = qs.filter(pedido__cliente__razao_social__icontains=filtros['cliente'])
        if filtros.get('produto'):
            qs = qs.filter(item__produto__nome__icontains=filtros['produto'])
        if filtros.get('prioridade'):
            qs = qs.filter(prioridade=filtros['prioridade'])
        return qs

    # ── Movimentação ─────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def mover(cls, ordem: OrdemProducao, chave_coluna: str, usuario) -> dict:
        """
        Leva a ordem para a coluna indicada e devolve o que mudou.

        Devolve, e não só grava, porque a tela precisa avisar quando o
        movimento concluiu etapas que ninguém apontou: arrastar de Corte
        direto para Qualidade fecha Sublimação, Costura e Acabamento, e
        fazer isso em silêncio encheria o relatório de produção com números
        que ninguém digitou.
        """
        if not usuario or not usuario.tem_permissao('moda', 'editar'):
            raise DomainError('Seu perfil não pode movimentar ordens no quadro.')

        coluna = COLUNAS_POR_CHAVE.get(chave_coluna)
        if coluna is None:
            raise DomainError('Coluna inválida.')

        if ordem.status == StatusOrdem.CANCELADA:
            raise DomainError('Ordem cancelada não se movimenta no quadro.')

        etapas = {e.etapa: e for e in ordem.etapas.all()}
        if not etapas:
            raise DomainError(
                'Esta ordem não tem fluxo montado. Rode criar_etapas_fluxo antes.'
            )

        alvo = etapas.get(coluna.destino)
        if alvo is None:
            raise DomainError('Esta ordem não tem a etapa desta coluna.')

        posicao_alvo = ORDEM_DAS_ETAPAS[coluna.destino]
        hoje = timezone.localdate()
        concluidas, reabertas = [], []

        for etapa in etapas.values():
            posicao = ORDEM_DAS_ETAPAS[etapa.etapa]

            if posicao < posicao_alvo:
                # Etapa anterior ao destino: se ainda estava aberta, o
                # movimento afirma que ela acabou.
                if etapa.status in (S.PENDENTE, S.EM_ANDAMENTO):
                    cls._concluir(etapa, hoje, usuario)
                    concluidas.append(etapa.get_etapa_display())

            elif posicao > posicao_alvo:
                # Etapa posterior: se estava concluída, o cartão voltou.
                # Os números apontados ficam — só o status volta.
                if etapa.status == S.CONCLUIDA:
                    etapa.status = S.PENDENTE
                    etapa.data_conclusao = None
                    etapa.atualizado_por = usuario
                    etapa.save(update_fields=['status', 'data_conclusao', 'atualizado_por'])
                    reabertas.append(etapa.get_etapa_display())

        # A etapa de destino: concluída na última coluna, em andamento nas
        # demais. "Concluído" é o fim do fluxo, não uma etapa em execução.
        if chave_coluna == 'concluido':
            if alvo.status != S.CONCLUIDA:
                cls._concluir(alvo, hoje, usuario)
        elif alvo.status not in (S.EM_ANDAMENTO, S.PULADA):
            alvo.status = S.EM_ANDAMENTO
            if not alvo.data_inicio:
                alvo.data_inicio = hoje
            alvo.data_conclusao = None
            alvo.atualizado_por = usuario
            alvo.save(update_fields=[
                'status', 'data_inicio', 'data_conclusao', 'atualizado_por',
            ])

        status_ordem = cls._sincronizar_ordem(ordem, chave_coluna, usuario)

        return {
            'coluna': chave_coluna,
            'concluidas': concluidas,
            'reabertas': reabertas,
            'status_ordem': status_ordem,
        }

    @staticmethod
    def _concluir(etapa: EtapaOrdem, hoje, usuario) -> None:
        """
        Fecha a etapa assumindo o planejado, quando nada foi apontado.

        Mesma regra do apontamento manual: etapa administrativa e etapa
        pulada por engano não têm produção digitada, e exigir o número aqui
        travaria o quadro no meio de um arrasto.
        """
        etapa.status = S.CONCLUIDA
        if not etapa.data_inicio:
            etapa.data_inicio = hoje
        if not etapa.data_conclusao:
            etapa.data_conclusao = hoje
        if not etapa.quantidade_produzida and not etapa.perda:
            etapa.quantidade_produzida = etapa.planejada
        etapa.atualizado_por = usuario
        etapa.save()

    @staticmethod
    def _sincronizar_ordem(ordem, chave_coluna: str, usuario) -> str:
        """
        Ajusta o status da OP conforme a coluna — o que a especificação pede
        com "ao movimentar o card, atualizar automaticamente o status da OP".

        Caminha pelas transições permitidas uma a uma em vez de gravar o
        status final direto: cada passo continua passando pela mesma regra de
        fluxo da OP, e um destino inalcançável para de existir em vez de ser
        forçado pela porta dos fundos.
        """
        from apps.moda.services.ordem import TRANSICOES

        alvo = (
            StatusOrdem.CONCLUIDA if chave_coluna == 'concluido'
            else StatusOrdem.EM_PRODUCAO
        )
        if ordem.status == alvo:
            return ordem.status

        # No máximo o tamanho do grafo: impede laço infinito se alguém
        # acrescentar um ciclo em TRANSICOES.
        for _ in range(len(TRANSICOES)):
            if ordem.status == alvo:
                break
            proximos = [
                s for s in TRANSICOES.get(ordem.status, ())
                if s != StatusOrdem.CANCELADA
            ]
            if not proximos:
                break
            ordem.status = alvo if alvo in proximos else proximos[0]

        ordem.save(update_fields=['status', 'updated_at'])
        return ordem.status
