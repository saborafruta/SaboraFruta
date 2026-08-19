"""
A linha do tempo de um pedido — quem fez o quê, quando, e o que mudou.

LÊ O `LogSistema` QUE JÁ EXISTE. Os signals de auditoria do `core` gravam,
a cada save e delete, o antes e o depois campo a campo, com usuário, IP e
filial. Uma tabela de histórico própria seria uma segunda verdade sobre o
mesmo fato — e a primeira vez que divergissem, ninguém saberia qual valia.

O QUE ESTE MÓDULO ACRESCENTA É A LEITURA. O log cru diz:

    EDITAR moda_etapas_ordem #412  status: "Pendente" → "Em Andamento"

e o que a fábrica precisa ler é:

    19/08/2026 15:40  Maria   Corte iniciado

A tradução acontece em `FRASES`: (tabela, campo, valor) → frase. O que não
tem frase própria não some — vira "Etapa de corte alterada" com o campo, o
valor anterior e o novo logo abaixo. Esconder seria pior do que ser seco.

COMO OS EVENTOS SÃO ENCONTRADOS: partindo do pedido, coletamos os ids de
tudo que pende dele (itens, artes, ordens, etapas, cortes, inspeções,
expedição) e buscamos o log por (tabela, id). É o caminho que funciona sem
depender do conteúdo do JSON, que guarda a chave estrangeira já convertida
em texto.

LIMITE CONHECIDO: registro APAGADO não é alcançado por esse caminho — ele
não está mais pendurado no pedido. Por isso há uma segunda busca, só para
exclusões, casando o nome do pedido dentro do snapshot. Ela depende de
consulta em JSON (Postgres) e está isolada num `try`: onde não funcionar, a
linha do tempo perde as exclusões de filhos e continua de pé.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import models as djm

from apps.core.models import LogSistema
from apps.moda.models import (
    AprovacaoPedido, EtapaOrdem, Expedicao, Inspecao, ItemConferencia, ItemCorte,
    ItemGradePedido, ItemInspecao, OrdemProducao, PedidoProducao,
    Personalizacao, PersonalizacaoIndividual, RegistroCorte, VisualItemPedido,
    Volume,
)

# Campos que não contam história: chave, carimbo automático, escopo de
# filial. Aparecer aqui só empurraria o que interessa para baixo.
IGNORADOS = {
    'id', 'filial', 'filial_id', 'empresa', 'empresa_id',
    'criado_em', 'created_at', 'atualizado_em', 'updated_at',
    'criado_por', 'atualizado_por', 'token_publico', 'codigo_qr',
    'id_externo', 'grupo_replicacao', 'ordem', 'sequencia',
}

# Nome legível de cada tabela auditada do vertical.
ENTIDADES = {
    PedidoProducao: 'Pedido',
    AprovacaoPedido: 'Aprovação',
    ItemGradePedido: 'Grade',
    Personalizacao: 'Arte',
    VisualItemPedido: 'Visual',
    PersonalizacaoIndividual: 'Personalização individual',
    OrdemProducao: 'Ordem de produção',
    EtapaOrdem: 'Etapa',
    RegistroCorte: 'Corte',
    ItemCorte: 'Item do corte',
    Inspecao: 'Inspeção',
    ItemInspecao: 'Ponto da inspeção',
    Expedicao: 'Expedição',
    ItemConferencia: 'Conferência',
    Volume: 'Volume',
}
POR_TABELA = {m._meta.db_table: (m, rotulo) for m, rotulo in ENTIDADES.items()}


@dataclass
class Mudanca:
    campo: str
    antes: str
    depois: str


@dataclass
class Evento:
    quando: datetime
    usuario: str
    titulo: str
    entidade: str
    acao: str
    mudancas: list[Mudanca] = field(default_factory=list)
    # Marco = etapa vencida ("Corte concluído"), não ajuste de campo. A tela
    # destaca os marcos: é essa distinção que faz a linha do tempo ser lida
    # em cinco segundos em vez de percorrida inteira.
    marco: bool = False


# ── Tradução ─────────────────────────────────────────────────────────────
# (tabela, campo, valor novo) → frase. O valor vem do log já com o rótulo do
# choice ("Em Andamento"), porque é assim que o serializador do `core` grava.

def _frases_etapa():
    """As onze etapas do fluxo × iniciada/concluída, sem escrever 22 linhas."""
    frases = {}
    for valor, rotulo in EtapaOrdem.Etapa.choices:
        frases[('Em Andamento', valor)] = f'{rotulo} iniciado'
        frases[('Concluída', valor)] = f'{rotulo} concluído'
        frases[('Bloqueada', valor)] = f'{rotulo} bloqueado'
    return frases


FRASES_ETAPA = _frases_etapa()

FRASES = {
    (PedidoProducao._meta.db_table, 'status'): {
        'Pedido Confirmado': 'Pedido confirmado',
        'Aguardando Arte': 'Aguardando arte',
        'Aguardando Material': 'Aguardando material',
        'Liberado para Produção': 'Pedido liberado para produção',
        'Em Produção': 'Produção iniciada',
        'Em Acabamento': 'Em acabamento',
        'Pronto': 'Pedido pronto',
        'Entregue': 'Pedido entregue',
        'Cancelado': 'Pedido cancelado',
    },
    (OrdemProducao._meta.db_table, 'status'): {
        'Liberada': 'Ordem liberada para a fábrica',
        'Em Produção': 'Ordem em produção',
        'Concluída': 'Ordem concluída',
        'Cancelada': 'Ordem cancelada',
    },
    (RegistroCorte._meta.db_table, 'status'): {
        'Cortado': 'Corte concluído',
        'Cancelado': 'Corte cancelado',
    },
    (Inspecao._meta.db_table, 'status'): {
        'Aprovado': 'Inspeção aprovada',
        'Reprovado': 'Inspeção reprovada',
        'Retrabalho': 'Enviado para retrabalho',
    },
    (AprovacaoPedido._meta.db_table, 'resposta'): {
        'Aprovado pelo cliente': 'Cliente aprovou o pedido',
        'Cliente pediu ajuste': 'Cliente pediu ajuste',
    },
    (Expedicao._meta.db_table, 'status'): {
        'Conferência': 'Conferência iniciada',
        'Separação': 'Separação iniciada',
        'Embalagem': 'Embalagem iniciada',
        'Despachado': 'Pedido despachado',
        'Entregue': 'Pedido entregue ao cliente',
    },
}

# Criação de um registro: a frase que a fábrica usa.
FRASES_CRIACAO = {
    PedidoProducao._meta.db_table: 'Pedido criado',
    ItemGradePedido._meta.db_table: 'Grade lançada',
    Personalizacao._meta.db_table: 'Arte anexada',
    VisualItemPedido._meta.db_table: 'Visual anexado',
    PersonalizacaoIndividual._meta.db_table: 'Personalização individual lançada',
    OrdemProducao._meta.db_table: 'Ordem de produção emitida',
    RegistroCorte._meta.db_table: 'Corte registrado',
    Inspecao._meta.db_table: 'Inspeção aberta',
    Expedicao._meta.db_table: 'Expedição aberta',
    Volume._meta.db_table: 'Volume criado',
    AprovacaoPedido._meta.db_table: 'Pedido liberado para o cliente',
}

FRASES_EXCLUSAO = {
    ItemGradePedido._meta.db_table: 'Tamanho removido da grade',
    Personalizacao._meta.db_table: 'Arte removida',
    VisualItemPedido._meta.db_table: 'Visual removido',
    Volume._meta.db_table: 'Volume removido',
}


class HistoricoService:

    # ── Entrada ──────────────────────────────────────────────────────────

    @classmethod
    def do_pedido(cls, pedido) -> list[Evento]:
        return cls._montar(cls._alvos_do_pedido(pedido), str(pedido))

    @classmethod
    def da_ordem(cls, ordem) -> list[Evento]:
        return cls._montar(cls._alvos_da_ordem(ordem), str(ordem))

    # ── Quem pende de quem ───────────────────────────────────────────────

    @staticmethod
    def _alvos_do_pedido(pedido) -> dict[str, list[int]]:
        """
        Tabela → ids de tudo que conta a história DESTE pedido.

        Uma consulta por família, com `values_list`: carregar os objetos
        inteiros só para pegar a chave seria dezenas de SELECTs completos
        numa tela que não mostra nenhum deles.
        """
        itens = list(pedido.itens.values_list('pk', flat=True))
        ordens = list(
            OrdemProducao.all_objects.filter(pedido=pedido).values_list('pk', flat=True)
        )
        cortes = list(
            RegistroCorte.all_objects.filter(ordem__in=ordens).values_list('pk', flat=True)
        )
        inspecoes = list(
            Inspecao.all_objects.filter(ordem__in=ordens).values_list('pk', flat=True)
        )
        expedicoes = list(
            Expedicao.objects.filter(ordem__in=ordens).values_list('pk', flat=True)
        )

        return {
            PedidoProducao._meta.db_table: [pedido.pk],
            ItemGradePedido._meta.db_table: _ids(ItemGradePedido, item__in=itens),
            Personalizacao._meta.db_table: _ids(Personalizacao, item__in=itens),
            VisualItemPedido._meta.db_table: _ids(VisualItemPedido, item__in=itens),
            PersonalizacaoIndividual._meta.db_table: _ids(
                PersonalizacaoIndividual, pedido=pedido,
            ),
            OrdemProducao._meta.db_table: ordens,
            EtapaOrdem._meta.db_table: _ids(EtapaOrdem, ordem__in=ordens),
            RegistroCorte._meta.db_table: cortes,
            ItemCorte._meta.db_table: _ids(ItemCorte, corte__in=cortes),
            Inspecao._meta.db_table: inspecoes,
            ItemInspecao._meta.db_table: _ids(ItemInspecao, inspecao__in=inspecoes),
            Expedicao._meta.db_table: expedicoes,
            ItemConferencia._meta.db_table: _ids(
                ItemConferencia, expedicao__in=expedicoes,
            ),
            Volume._meta.db_table: _ids(Volume, expedicao__in=expedicoes),
        }

    @staticmethod
    def _alvos_da_ordem(ordem) -> dict[str, list[int]]:
        cortes = _ids(RegistroCorte, ordem=ordem, manager='all_objects')
        inspecoes = _ids(Inspecao, ordem=ordem, manager='all_objects')
        expedicoes = _ids(Expedicao, ordem=ordem)
        return {
            OrdemProducao._meta.db_table: [ordem.pk],
            EtapaOrdem._meta.db_table: _ids(EtapaOrdem, ordem=ordem),
            RegistroCorte._meta.db_table: cortes,
            ItemCorte._meta.db_table: _ids(ItemCorte, corte__in=cortes),
            Inspecao._meta.db_table: inspecoes,
            ItemInspecao._meta.db_table: _ids(ItemInspecao, inspecao__in=inspecoes),
            Expedicao._meta.db_table: expedicoes,
            Volume._meta.db_table: _ids(Volume, expedicao__in=expedicoes),
        }

    # ── Montagem ─────────────────────────────────────────────────────────

    @classmethod
    def _montar(cls, alvos: dict[str, list[int]], nome_raiz: str) -> list[Evento]:
        logs = list(cls._buscar(alvos))
        logs += cls._exclusoes_orfas(alvos, nome_raiz, {l.pk for l in logs})

        eventos = [cls._evento(log) for log in logs]
        eventos = [e for e in eventos if e is not None]
        # Ordem cronológica: a linha do tempo se lê de cima para baixo, como
        # a ficha de papel que ela substitui.
        eventos.sort(key=lambda e: e.quando)
        return eventos

    @staticmethod
    def _buscar(alvos):
        filtro = djm.Q(pk__in=[])
        for tabela, ids in alvos.items():
            if ids:
                filtro |= djm.Q(tabela_afetada=tabela, registro_id__in=ids)
        return (
            LogSistema.objects.filter(filtro)
            .select_related('usuario')
            .order_by('data_hora')
        )

    @staticmethod
    def _exclusoes_orfas(alvos, nome_raiz, ja_vistos) -> list:
        """
        As exclusões que o caminho normal não alcança.

        Um filho apagado não pende mais do pedido: o `_buscar` acima nunca
        chega nele. O snapshot do log guarda a chave estrangeira já
        convertida em texto (`"#000012 — INTERFORT"`), e é por esse texto que
        dá para reencontrá-lo.

        Consulta em JSON funciona no Postgres, que é o banco de produção. Se
        o banco não suportar, a linha do tempo fica sem as exclusões de
        filhos em vez de quebrar — por isso o `try`.
        """
        if not nome_raiz:
            return []
        try:
            achados = list(
                LogSistema.objects
                .filter(
                    acao=LogSistema.Acao.EXCLUIR,
                    tabela_afetada__in=list(alvos),
                )
                .filter(
                    djm.Q(dados_anteriores__pedido=nome_raiz)
                    | djm.Q(dados_anteriores__ordem=nome_raiz)
                )
                .exclude(pk__in=ja_vistos)
                .select_related('usuario')
            )
        except Exception:
            return []
        return achados

    # ── Um log vira um evento ────────────────────────────────────────────

    @classmethod
    def _evento(cls, log) -> Evento | None:
        info = POR_TABELA.get(log.tabela_afetada)
        if info is None:
            return None
        modelo, rotulo = info

        mudancas = cls._mudancas(modelo, log)
        if log.acao == LogSistema.Acao.EDITAR and not mudancas:
            # Gravação que não mudou nada: acontece toda vez que um
            # formulário é salvo sem alteração, e listar isso encheria a
            # linha do tempo de "editado" vazio.
            return None

        titulo, e_marco = cls._titulo(log, rotulo, mudancas)
        return Evento(
            quando=log.data_hora,
            usuario=getattr(log.usuario, 'nome', '') or 'Sistema',
            titulo=titulo,
            entidade=rotulo,
            acao=log.acao,
            mudancas=mudancas,
            marco=e_marco,
        )

    @staticmethod
    def _titulo(log, rotulo, mudancas) -> tuple[str, bool]:
        tabela = log.tabela_afetada

        if log.acao == LogSistema.Acao.CRIAR:
            return FRASES_CRIACAO.get(tabela, f'{rotulo} criado'), True
        if log.acao == LogSistema.Acao.EXCLUIR:
            return FRASES_EXCLUSAO.get(tabela, f'{rotulo} removido'), True

        novos = log.dados_novos or {}

        # Etapa do fluxo: a frase depende do status E de qual etapa é.
        if tabela == EtapaOrdem._meta.db_table:
            chave = (novos.get('status'), _valor_bruto(EtapaOrdem, 'etapa', novos))
            if chave in FRASES_ETAPA and any(m.campo == 'Status' for m in mudancas):
                return FRASES_ETAPA[chave], True

        # Campo com frase própria — só quando ELE é o que de fato mudou:
        # salvar outro campo não deve reanunciar "Pedido confirmado" toda
        # vez. O campo varia por tabela (`status` na maioria, `resposta` na
        # aprovação do cliente), então a busca é pela chave da tabela.
        mudados = {m.campo for m in mudancas}
        for (tab, campo), dicionario in FRASES.items():
            if tab != tabela:
                continue
            if ROTULOS.get(campo, campo.capitalize()) not in mudados:
                continue
            frase = dicionario.get(novos.get(campo))
            if frase:
                return frase, True

        if len(mudancas) == 1:
            return f'{rotulo}: {mudancas[0].campo.lower()} alterado', False
        return f'{rotulo} alterado', False

    @staticmethod
    def _mudancas(modelo, log) -> list[Mudanca]:
        if log.acao != LogSistema.Acao.EDITAR:
            return []

        novos = log.dados_novos or {}
        anteriores = log.dados_anteriores or {}
        rotulos = _rotulos(modelo)

        mudancas = []
        for campo, depois in novos.items():
            if campo in IGNORADOS:
                continue
            antes = anteriores.get(campo)
            if _iguais(modelo, campo, antes, depois):
                continue
            mudancas.append(Mudanca(
                campo=rotulos.get(campo, campo.replace('_', ' ').capitalize()),
                antes=_texto(antes),
                depois=_texto(depois),
            ))
        return mudancas


# ── Auxiliares ───────────────────────────────────────────────────────────

def _ids(modelo, manager: str = 'objects', **filtros) -> list[int]:
    consulta = getattr(modelo, manager, None) or modelo.objects
    return list(consulta.filter(**filtros).values_list('pk', flat=True))


# O `verbose_name` automático do Django é o nome do campo sem underscore, e
# sai sem acento: "observacoes", "acrescimo", "personalizacoes". Numa tela de
# auditoria, que é lida por quem está conferindo o que alguém fez, escrever
# errado é ruído desnecessário.
ROTULOS = {
    'observacoes': 'Observações',
    'observacao': 'Observação',
    'acrescimo': 'Acréscimo',
    'descricao': 'Descrição',
    'endereco': 'Endereço',
    'numero': 'Número',
    'quantidade_produzida': 'Quantidade produzida',
    'quantidade_planejada': 'Quantidade planejada',
    'quantidade_inspecionada': 'Quantidade inspecionada',
    'quantidade_aprovada': 'Quantidade aprovada',
    'quantidade_reprovada': 'Quantidade reprovada',
    'quantidade_retrabalho': 'Quantidade em retrabalho',
    'aproveitamento': 'Aproveitamento',
    'consumo_real': 'Consumo real',
    'consumo_planejado': 'Consumo planejado',
    'data_conclusao': 'Data de conclusão',
    'data_inicio': 'Data de início',
    'data_prevista': 'Data prevista',
    'data_prevista_entrega': 'Entrega prevista',
    'tempo_minutos': 'Tempo (minutos)',
    'valor_unitario': 'Valor unitário',
    'condicao_pagamento': 'Condição de pagamento',
    'forma_pagamento': 'Forma de pagamento',
    'prioridade': 'Prioridade',
    'responsavel': 'Responsável',
    'maquina': 'Máquina',
    'perda': 'Perda',
    'lote': 'Lote',
    'motivo': 'Motivo',
    'status': 'Status',
    'resposta': 'Resposta',
}


def _rotulos(modelo) -> dict[str, str]:
    return {
        f.name: ROTULOS.get(f.name, f.verbose_name.capitalize())
        for f in modelo._meta.concrete_fields
    }


def _valor_bruto(modelo, campo, dados):
    """
    O valor de um choice de volta ao código.

    O serializador do log grava o RÓTULO ("Sublimação / Bordado / Silk"), que
    é o certo para exibir e inútil para comparar. Aqui ele volta a ser
    `estampa`, que é o que a tabela de frases usa como chave.
    """
    rotulo = dados.get(campo)
    try:
        escolhas = modelo._meta.get_field(campo).choices or ()
    except Exception:
        return rotulo
    for valor, texto in escolhas:
        if texto == rotulo:
            return valor
    return rotulo


def _numerico(modelo, campo) -> bool:
    try:
        f = modelo._meta.get_field(campo)
    except Exception:
        return False
    return isinstance(f, (
        djm.DecimalField, djm.FloatField, djm.IntegerField,
        djm.PositiveIntegerField, djm.PositiveSmallIntegerField,
        djm.SmallIntegerField, djm.BigIntegerField,
    ))


def _decimal(valor):
    try:
        return Decimal(str(valor))
    except (TypeError, ValueError, InvalidOperation):
        return None


def _iguais(modelo, campo, antes, depois) -> bool:
    """
    "10.00" e "10" são o mesmo número.

    Sem esta comparação, todo salvamento de um formulário com decimal
    apareceria como alteração — o Django devolve `Decimal('10.00')` e o
    formulário manda `10`, e a linha do tempo viraria ruído.
    """
    if antes == depois:
        return True
    if _numerico(modelo, campo):
        a, b = _decimal(antes), _decimal(depois)
        if a is not None and b is not None:
            return a == b
    return False


def _texto(valor) -> str:
    if valor in (None, ''):
        return '—'
    if isinstance(valor, bool):
        return 'Sim' if valor else 'Não'
    return str(valor)
