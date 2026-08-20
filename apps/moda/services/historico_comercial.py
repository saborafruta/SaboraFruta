"""
Tudo que já passou pelo comercial — a linha do tempo da carteira inteira.

A LINHA DO TEMPO DE UM PEDIDO JÁ EXISTE (`HistoricoService.do_pedido`), e é
ela que responde "o que aconteceu NESTE pedido". Esta tela responde a outra
pergunta, que é a de quem gerencia: **o que aconteceu no comercial hoje?**
Quem mexeu, em quê, e quando.

LÊ O MESMO `LogSistema`, e traduz com as MESMAS frases. Uma segunda leitura
faria a tela do pedido dizer "Corte iniciado" e esta dizer outra coisa sobre
o mesmo registro -- e a primeira vez que divergissem, ninguém saberia qual
acreditar.

O RECORTE É COMERCIAL, não o vertical inteiro: pedido, aprovação, grade,
arte e personalização individual. Corte, inspeção e expedição têm suas
próprias telas e encheriam esta com apontamento de fábrica -- que é
exatamente o que quem está no comercial não está procurando aqui.

LIMITE DE PERÍODO SEMPRE. Sem ele a consulta cresce para sempre e a tela
fica mais lenta a cada mês de uso, até alguém desistir de abri-la.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import timedelta

from django.utils import timezone

from apps.core.models import LogSistema
from apps.moda.models import (
    AprovacaoPedido, ItemGradePedido, ItemPedidoProducao, PedidoProducao,
    Personalizacao, PersonalizacaoIndividual, VisualItemPedido,
)
from apps.moda.services.historico import HistoricoService

# As tabelas do comercial, e de quem cada linha depende para achar o pedido.
# `None` significa que o próprio registro é o pedido.
TABELAS = OrderedDict([
    (PedidoProducao._meta.db_table, None),
    (AprovacaoPedido._meta.db_table, AprovacaoPedido),
    (ItemPedidoProducao._meta.db_table, ItemPedidoProducao),
    (ItemGradePedido._meta.db_table, ItemGradePedido),
    (Personalizacao._meta.db_table, Personalizacao),
    (VisualItemPedido._meta.db_table, VisualItemPedido),
    (PersonalizacaoIndividual._meta.db_table, PersonalizacaoIndividual),
])

# Quantos eventos a tela carrega de uma vez. Passa disso, é relatório, e
# relatório se filtra por período em vez de rolar para sempre.
LIMITE = 300

PERIODOS = [(7, '7 dias'), (30, '30 dias'), (90, '90 dias'), (365, '1 ano')]


class HistoricoComercialService:

    @classmethod
    def montar(cls, filial, dias: int = 30, busca: str = '',
               usuario_id: str = '', acao: str = '') -> dict:
        desde = timezone.now() - timedelta(days=dias)

        consulta = (
            LogSistema.objects
            .filter(filial=filial, tabela_afetada__in=TABELAS.keys(),
                    data_hora__gte=desde)
            .select_related('usuario')
            .order_by('-data_hora')
        )
        if usuario_id.isdigit():
            consulta = consulta.filter(usuario_id=int(usuario_id))
        if acao:
            consulta = consulta.filter(acao=acao)

        if busca:
            # A busca é POR PEDIDO: os alvos de cada pedido encontrado saem
            # do mesmo mapeamento que a linha do tempo individual usa, e não
            # de uma segunda regra escrita aqui.
            alvos = cls._alvos_da_busca(filial, busca)
            if not alvos:
                return cls._vazio(dias, busca)
            filtro = None
            from django.db.models import Q

            for tabela, ids in alvos.items():
                if not ids:
                    continue
                parte = Q(tabela_afetada=tabela, registro_id__in=ids)
                filtro = parte if filtro is None else (filtro | parte)
            consulta = consulta.filter(filtro) if filtro is not None else consulta.none()

        logs = list(consulta[:LIMITE])
        pedidos = cls._pedidos_dos_logs(filial, logs)

        eventos = []
        for log in logs:
            evento = HistoricoService._evento(log)
            if evento is None:
                # Gravação que não mudou nada -- o serviço já descarta, e
                # listar isso encheria a tela de "editado" vazio.
                continue
            eventos.append({
                'evento': evento,
                'pedido': pedidos.get((log.tabela_afetada, log.registro_id)),
            })

        return {
            'dias': dias,
            'busca': busca,
            'periodos': PERIODOS,
            'acoes': LogSistema.Acao.choices,
            'dias_por_dia': cls._agrupar(eventos),
            'total': len(eventos),
            'pessoas': sorted({e['evento'].usuario for e in eventos}),
            'pedidos_tocados': len({
                p.pk for p in pedidos.values() if p is not None
            }),
            'cortado': len(logs) >= LIMITE,
            'limite': LIMITE,
        }

    @staticmethod
    def _vazio(dias, busca) -> dict:
        return {
            'dias': dias, 'busca': busca, 'periodos': PERIODOS,
            'acoes': LogSistema.Acao.choices, 'dias_por_dia': [],
            'total': 0, 'pessoas': [], 'pedidos_tocados': 0,
            'cortado': False, 'limite': LIMITE,
        }

    @staticmethod
    def _agrupar(eventos) -> list:
        """
        Por dia, do mais recente para trás.

        A leitura desta tela é "o que aconteceu ontem", e uma lista corrida
        de trezentas linhas com a data repetida em cada uma não responde
        isso — o olho procura o dia primeiro.
        """
        por_dia = OrderedDict()
        for item in eventos:
            dia = timezone.localtime(item['evento'].quando).date()
            por_dia.setdefault(dia, []).append(item)
        return [{'dia': dia, 'eventos': lista} for dia, lista in por_dia.items()]

    @staticmethod
    def _alvos_da_busca(filial, busca: str) -> dict:
        from django.db.models import Q

        pedidos = list(
            PedidoProducao.objects.for_filial(filial).filter(
                Q(numero__icontains=busca)
                | Q(cliente__razao_social__icontains=busca)
                | Q(cliente__nome_fantasia__icontains=busca)
            )[:20]
        )
        alvos = {}
        for pedido in pedidos:
            for tabela, ids in HistoricoService._alvos_do_pedido(pedido).items():
                alvos.setdefault(tabela, []).extend(ids)
        return alvos

    @staticmethod
    def _pedidos_dos_logs(filial, logs) -> dict:
        """
        De qual pedido é cada evento.

        Em uma consulta por tabela, e não uma por linha: trezentos eventos
        dariam trezentas consultas, e a tela que mostra o histórico seria a
        mais lenta do sistema.

        Registro APAGADO não é alcançado -- ele não existe mais para
        apontar o dono. O evento continua na lista, sem link: sumir com ele
        seria esconder justamente a exclusão.
        """
        ids_por_tabela = {}
        for log in logs:
            if log.registro_id is None:
                continue
            ids_por_tabela.setdefault(log.tabela_afetada, set()).add(log.registro_id)

        achados = {}
        cache_pedidos = {}

        for tabela, ids in ids_por_tabela.items():
            modelo = TABELAS.get(tabela, 'ausente')
            if modelo == 'ausente':
                continue

            if modelo is None:
                for pedido in PedidoProducao.objects.for_filial(filial).filter(pk__in=ids):
                    cache_pedidos[pedido.pk] = pedido
                    achados[(tabela, pedido.pk)] = pedido
                continue

            caminho = 'item__pedido' if _tem_item(modelo) else 'pedido'
            for registro in modelo.objects.filter(pk__in=ids).select_related(caminho):
                pedido = _pedido_de(registro)
                if pedido is None or pedido.filial_id != filial.pk:
                    continue
                cache_pedidos[pedido.pk] = pedido
                achados[(tabela, registro.pk)] = pedido

        return achados


def _tem_item(modelo) -> bool:
    return any(f.name == 'item' for f in modelo._meta.get_fields())


def _pedido_de(registro):
    if hasattr(registro, 'pedido_id'):
        return registro.pedido
    item = getattr(registro, 'item', None)
    return getattr(item, 'pedido', None)
