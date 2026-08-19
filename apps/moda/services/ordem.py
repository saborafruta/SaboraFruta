"""
Emissão e edição da Ordem de Produção.

A parte que merece leitura é `CAMPOS`: a especificação pede "editar apenas
os campos autorizados pelo perfil", e a autorização aqui não é um sistema
novo — reaproveita as ações que `Permissao` já tem (`pode_editar`,
`pode_aprovar`, `pode_cancelar`). Inventar um controle paralelo de campos
significaria dois lugares para conceder acesso, e o dia em que os dois
discordassem ninguém saberia qual vale.

A divisão segue o que cada campo faz na fábrica:

  - `observacoes` é recado, não muda o que se produz → `editar`;
  - `quantidade`, `prazo` e `prioridade` mudam o compromisso assumido com o
    cliente e a fila de todo mundo → `aprovar`;
  - cancelar a ordem → `cancelar`.

O servidor decide, não a tela. A tela esconde o que o usuário não pode
mexer, mas quem barra de verdade é `aplicar()`: campo não autorizado é
descartado em silêncio no servidor, e um POST montado à mão não passa.
"""
from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError

from .validacao import ValidacaoProducao
from apps.moda.models import OrdemProducao

MODULO = 'moda'

# campo -> ação exigida em `Permissao` para aquele campo
CAMPOS: dict[str, str] = {
    'observacoes': 'editar',
    'quantidade': 'aprovar',
    'prazo': 'aprovar',
    'prioridade': 'aprovar',
}

# Transições permitidas. Um dicionário e não `if`s espalhados: assim a
# regra inteira do fluxo cabe numa tela e some o risco de dois caminhos
# concordarem sobre status diferentes.
TRANSICOES: dict[str, tuple[str, ...]] = {
    OrdemProducao.Status.EMITIDA: (
        OrdemProducao.Status.LIBERADA, OrdemProducao.Status.CANCELADA,
    ),
    OrdemProducao.Status.LIBERADA: (
        OrdemProducao.Status.EM_PRODUCAO, OrdemProducao.Status.CANCELADA,
    ),
    OrdemProducao.Status.EM_PRODUCAO: (
        OrdemProducao.Status.CONCLUIDA, OrdemProducao.Status.CANCELADA,
    ),
    OrdemProducao.Status.CONCLUIDA: (),
    OrdemProducao.Status.CANCELADA: (),
}


class OrdemProducaoService:

    # ── Autorização por campo ────────────────────────────────────────────

    @staticmethod
    def campos_editaveis(usuario) -> set[str]:
        """Quais dos campos da OP este usuário pode alterar."""
        if usuario is None:
            return set()
        return {
            campo for campo, acao in CAMPOS.items()
            if usuario.tem_permissao(MODULO, acao)
        }

    @staticmethod
    def pode_cancelar(usuario) -> bool:
        return bool(usuario) and usuario.tem_permissao(MODULO, 'cancelar')

    # ── Emissão ──────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def gerar_do_pedido(cls, pedido, usuario=None) -> list[OrdemProducao]:
        """
        Emite uma OP para cada item do pedido que ainda não tem uma aberta.

        Uma por ITEM, e não uma por pedido, porque é o produto que caminha
        pela fábrica: cada um tem ficha, roteiro e grade próprios, e o corte
        precisa de uma folha por peça, não de uma folha com tudo junto.

        Item que já tem ordem aberta é pulado em vez de duplicado — emitir
        de novo por engano é o erro mais fácil de cometer nesta tela, e ele
        colocaria a mesma peça duas vezes na fila.
        """
        if pedido.status == pedido.Status.CANCELADO:
            raise DomainError('Pedido cancelado não gera ordem de produção.')

        # A TRAVA. Emitir a OP é o momento em que o pedido vira tecido
        # cortado, e daí não volta com um Ctrl+Z: as onze validações são
        # cobradas aqui, e não só na tela, porque a tela não é o único
        # caminho até este serviço.
        ValidacaoProducao.exigir(pedido)

        itens = list(pedido.itens.all())
        if not itens:
            raise DomainError('O pedido não tem produtos para produzir.')

        ja_abertas = set(
            OrdemProducao.objects
            .filter(pedido=pedido)
            .exclude(status__in=OrdemProducao.STATUS_ENCERRADOS)
            .values_list('item_id', flat=True)
        )
        pendentes = [i for i in itens if i.pk not in ja_abertas]
        if not pendentes:
            raise DomainError(
                'Todos os produtos deste pedido já têm ordem de produção aberta.'
            )

        ano = timezone.localdate().year
        proximo = cls._proximo_sequencial(pedido.filial_id, ano)

        criadas = []
        for posicao, item in enumerate(pendentes):
            sequencial = proximo + posicao
            criadas.append(OrdemProducao(
                filial=pedido.filial,
                ano=ano,
                sequencial=sequencial,
                numero=OrdemProducao.montar_numero(ano, sequencial),
                pedido=pedido,
                item=item,
                quantidade=item.quantidade,
                prazo=pedido.data_prevista_entrega,
                prioridade=pedido.prioridade,
                observacoes=item.observacoes or '',
                emitida_por=usuario,
            ))

        # `bulk_create` não chama `save()`, por isso o número é montado
        # acima em vez de ficar só no `save()` do modelo.
        OrdemProducao.objects.bulk_create(criadas)

        # As etapas do fluxo nascem aqui, e não sob demanda: uma OP aberta
        # amanhã mostraria só as etapas que alguém tocou, e "não iniciada"
        # ficaria indistinguível de "não existe".
        from apps.moda.services.fluxo import FluxoService
        for ordem in criadas:
            FluxoService.criar_etapas(ordem)

        return criadas

    @staticmethod
    def _proximo_sequencial(filial_id, ano: int) -> int:
        """
        Próximo número do ano, por filial.

        Por filial e por ano: é assim que a fábrica referencia a ordem no
        chão, e reiniciar a cada ano mantém o número curto. O formato
        `OP-2026-000001` já carrega o ano, então não há ambiguidade.
        """
        from django.db.models import Max

        ultimo = (
            OrdemProducao.all_objects
            .filter(filial_id=filial_id, ano=ano)
            .aggregate(Max('sequencial'))['sequencial__max']
        )
        return (ultimo or 0) + 1

    # ── Edição ───────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def aplicar(cls, ordem: OrdemProducao, usuario, dados: dict) -> list[str]:
        """
        Grava apenas os campos que o perfil autoriza. Devolve o que mudou.

        Campo não autorizado é IGNORADO em silêncio, e não recusado com
        erro: a tela nem oferece esses campos, então um valor deles chegando
        aqui veio de requisição forjada — responder "não pode" confirmaria
        ao curioso que o campo existe.
        """
        if ordem.encerrada:
            raise DomainError(
                f'Ordem {ordem.get_status_display().lower()} não pode ser alterada.'
            )

        permitidos = cls.campos_editaveis(usuario)
        alterados = []

        for campo in CAMPOS:
            if campo not in permitidos or campo not in dados:
                continue
            novo = cls._converter(campo, dados[campo])
            if novo is cls._INVALIDO:
                raise DomainError(f'Valor inválido para {campo}.')
            if getattr(ordem, campo) != novo:
                setattr(ordem, campo, novo)
                alterados.append(campo)

        if alterados:
            ordem.save(update_fields=alterados + ['updated_at'])
        return alterados

    _INVALIDO = object()

    @classmethod
    def _converter(cls, campo: str, bruto):
        if campo == 'quantidade':
            try:
                valor = int(bruto)
            except (TypeError, ValueError):
                return cls._INVALIDO
            # Zero peça não é ordem de produção — é ordem cancelada, e essa
            # decisão tem caminho próprio, com permissão própria.
            return valor if valor > 0 else cls._INVALIDO

        if campo == 'prazo':
            texto = (bruto or '').strip() if isinstance(bruto, str) else bruto
            if not texto:
                return None
            if not isinstance(texto, str):
                return texto
            try:
                return datetime.strptime(texto, '%Y-%m-%d').date()
            except ValueError:
                return cls._INVALIDO

        if campo == 'prioridade':
            return bruto if bruto in OrdemProducao.Prioridade.values else cls._INVALIDO

        return (bruto or '').strip()

    # ── Fluxo ────────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def mudar_status(cls, ordem: OrdemProducao, novo: str, usuario) -> OrdemProducao:
        if novo not in OrdemProducao.Status.values:
            raise DomainError('Status inválido.')

        permitidos = TRANSICOES.get(ordem.status, ())
        if novo not in permitidos:
            raise DomainError(
                f'Uma ordem {ordem.get_status_display().lower()} não pode ir para '
                f'{OrdemProducao.Status(novo).label.lower()}.'
            )

        if novo == OrdemProducao.Status.CANCELADA and not cls.pode_cancelar(usuario):
            raise DomainError('Seu perfil não pode cancelar ordens de produção.')
        if novo != OrdemProducao.Status.CANCELADA and not usuario.tem_permissao(MODULO, 'editar'):
            raise DomainError('Seu perfil não pode alterar o andamento das ordens.')

        ordem.status = novo
        ordem.save(update_fields=['status', 'updated_at'])
        return ordem

    @staticmethod
    def proximos_status(ordem: OrdemProducao) -> list[tuple[str, str]]:
        """Os status para onde esta ordem pode ir, para a tela montar botões."""
        rotulos = dict(OrdemProducao.Status.choices)
        return [(s, rotulos[s]) for s in TRANSICOES.get(ordem.status, ())]
