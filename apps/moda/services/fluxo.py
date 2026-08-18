"""
Fluxo de produção: criação das etapas e apontamento do chão de fábrica.

O apontamento é o ponto sensível deste arquivo. Quem preenche é o
encarregado, no meio do turno, muitas vezes no celular — então a regra é
aceitar o que for razoável e recusar só o que produziria número errado no
relatório. Campo em branco não apaga o que já estava lá; número que passa do
planejado é recusado, porque produzir mais do que se cortou é impossível e
significa erro de digitação.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.moda.models import EtapaOrdem

MODULO = 'moda'

# Etapas que a ordem atravessa, na ordem. Sequência espaçada de 10 para
# caber uma etapa no meio depois sem renumerar as vizinhas.
SEQUENCIA = [(e.value, (i + 1) * 10) for i, e in enumerate(EtapaOrdem.Etapa)]

# Campo -> ação exigida em `Permissao`. Mesmo desenho da Ordem de Produção:
# quem aponta produção precisa de `editar`; mexer no PLANEJADO altera o
# compromisso e exige `aprovar`.
CAMPOS: dict[str, str] = {
    'status': 'editar',
    'responsavel': 'editar',
    'maquina': 'editar',
    'tempo_minutos': 'editar',
    'data_inicio': 'editar',
    'data_conclusao': 'editar',
    'quantidade_produzida': 'editar',
    'perda': 'editar',
    'observacao': 'editar',
    'data_prevista': 'aprovar',
    'quantidade_planejada': 'aprovar',
}

DATAS = ('data_inicio', 'data_prevista', 'data_conclusao')
# Decimal em vez de inteiro: meia hora de costura e' 30, mas 7,5 min de
# prensa por peca tambem precisa caber.
DECIMAIS = ('tempo_minutos',)
INTEIROS = ('quantidade_produzida', 'perda', 'quantidade_planejada')


class FluxoService:

    # ── Criação ──────────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def criar_etapas(ordem) -> list[EtapaOrdem]:
        """
        Cria as onze etapas de uma ordem, todas pendentes.

        Idempotente: etapa que já existe não é recriada nem alterada. Isso
        importa porque o comando de retrofit roda sobre ordens antigas, e
        recriar apagaria apontamento já feito.
        """
        existentes = set(ordem.etapas.values_list('etapa', flat=True))
        novas = [
            EtapaOrdem(ordem=ordem, etapa=etapa, sequencia=seq)
            for etapa, seq in SEQUENCIA
            if etapa not in existentes
        ]
        if novas:
            EtapaOrdem.objects.bulk_create(novas)
        return novas

    # ── Autorização ──────────────────────────────────────────────────────

    @staticmethod
    def campos_editaveis(usuario) -> set[str]:
        if usuario is None:
            return set()
        return {
            campo for campo, acao in CAMPOS.items()
            if usuario.tem_permissao(MODULO, acao)
        }

    # ── Apontamento ──────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def apontar(cls, etapa: EtapaOrdem, usuario, dados: dict) -> list[str]:
        """
        Grava o apontamento da etapa, respeitando o que o perfil autoriza.

        Devolve os campos alterados. Campo ausente do POST é ignorado — a
        tela envia um formulário por etapa, e tratar ausência como "limpar"
        apagaria o apontamento das outras.
        """
        if etapa.ordem.encerrada:
            raise DomainError(
                f'A ordem está {etapa.ordem.get_status_display().lower()} — '
                f'não aceita mais apontamento.'
            )

        permitidos = cls.campos_editaveis(usuario)
        alterados = []

        for campo in CAMPOS:
            if campo not in permitidos or campo not in dados:
                continue
            novo = cls._converter(campo, dados[campo])
            if novo is cls._INVALIDO:
                raise DomainError(f'Valor inválido em {campo.replace("_", " ")}.')
            if getattr(etapa, campo) != novo:
                setattr(etapa, campo, novo)
                alterados.append(campo)

        if not alterados:
            return []

        cls._validar(etapa)
        cls._carimbar_datas(etapa, alterados)

        etapa.atualizado_por = usuario
        etapa.save()
        return alterados

    _INVALIDO = object()

    @classmethod
    def _converter(cls, campo, bruto):
        if campo == 'status':
            return bruto if bruto in EtapaOrdem.Status.values else cls._INVALIDO

        if campo in DATAS:
            texto = (bruto or '').strip() if isinstance(bruto, str) else bruto
            if not texto:
                return None
            if not isinstance(texto, str):
                return texto
            try:
                return datetime.strptime(texto, '%Y-%m-%d').date()
            except ValueError:
                return cls._INVALIDO

        if campo in DECIMAIS:
            texto = (bruto or '').strip() if isinstance(bruto, str) else bruto
            if texto in ('', None):
                return None
            try:
                valor = Decimal(str(texto).replace(',', '.'))
            except InvalidOperation:
                return cls._INVALIDO
            return valor if valor >= 0 else cls._INVALIDO

        if campo in INTEIROS:
            texto = (bruto or '').strip() if isinstance(bruto, str) else bruto
            # Planejada aceita vazio: é assim que se devolve a herança da
            # etapa anterior. Produzida e perda não — vazio ali seria zero
            # disfarçado, e zerar produção sem querer é fácil demais.
            if texto in ('', None):
                return None if campo == 'quantidade_planejada' else cls._INVALIDO
            try:
                valor = int(texto)
            except (TypeError, ValueError):
                return cls._INVALIDO
            return valor if valor >= 0 else cls._INVALIDO

        return (bruto or '').strip()

    @staticmethod
    def _validar(etapa: EtapaOrdem) -> None:
        planejada = etapa.planejada
        total = etapa.quantidade_produzida + etapa.perda
        # Produzir mais do que se cortou é impossível: é erro de digitação, e
        # deixá-lo passar contamina produção, perda e custo real de uma vez.
        if planejada and total > planejada:
            raise DomainError(
                f'Produzido ({etapa.quantidade_produzida}) mais perda ({etapa.perda}) '
                f'dá {total}, acima do planejado ({planejada}) para esta etapa.'
            )

        if (
            etapa.data_inicio and etapa.data_conclusao
            and etapa.data_conclusao < etapa.data_inicio
        ):
            raise DomainError('A conclusão não pode ser anterior ao início.')

    @staticmethod
    def _carimbar_datas(etapa: EtapaOrdem, alterados: list[str]) -> None:
        """
        Preenche as datas óbvias quando o status muda.

        Quem está no chão de fábrica marca "em andamento" e segue trabalhando;
        exigir que digite a data de hoje é o tipo de atrito que faz o
        apontamento não ser feito — e apontamento não feito é pior do que
        data aproximada.
        """
        if 'status' not in alterados:
            return
        hoje = timezone.localdate()

        if etapa.status == EtapaOrdem.Status.EM_ANDAMENTO and not etapa.data_inicio:
            etapa.data_inicio = hoje
            alterados.append('data_inicio')

        if etapa.status == EtapaOrdem.Status.CONCLUIDA:
            if not etapa.data_inicio:
                etapa.data_inicio = hoje
                alterados.append('data_inicio')
            if not etapa.data_conclusao:
                etapa.data_conclusao = hoje
                alterados.append('data_conclusao')
            # Concluir sem apontar produção: assume que saiu o planejado.
            # É o caso comum das etapas administrativas (Pedido,
            # Planejamento), e obrigar a digitar "40" onze vezes faria o
            # usuário concluir tudo sem olhar.
            if not etapa.quantidade_produzida and not etapa.perda:
                etapa.quantidade_produzida = etapa.planejada
                alterados.append('quantidade_produzida')

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def resumo(ordem) -> dict:
        """Onde a ordem está, quanto já andou e quanto perdeu no caminho."""
        etapas = list(ordem.etapas.all())
        if not etapas:
            return {
                'etapas': [], 'atual': None, 'concluidas': 0, 'total': 0,
                'percentual': Decimal('0'), 'perda_total': 0,
                'percentual_perda': Decimal('0'), 'atrasadas': [],
            }

        # "Atual" é a primeira etapa não encerrada: é a resposta para "onde
        # está agora". Usar a última mexida daria a etapa que alguém tocou
        # por último, que não é a mesma pergunta.
        atual = next((e for e in etapas if not e.encerrada), None)

        contam = [e for e in etapas if e.status != EtapaOrdem.Status.PULADA]
        concluidas = [e for e in contam if e.concluida]
        perda_total = sum(e.perda for e in etapas)

        return {
            'etapas': etapas,
            'atual': atual,
            'concluidas': len(concluidas),
            'total': len(contam),
            'percentual': (
                (Decimal(len(concluidas)) / len(contam) * 100).quantize(Decimal('0.1'))
                if contam else Decimal('0')
            ),
            'perda_total': perda_total,
            'percentual_perda': (
                (Decimal(perda_total) / ordem.quantidade * 100).quantize(Decimal('0.1'))
                if ordem.quantidade else Decimal('0')
            ),
            'atrasadas': [e for e in etapas if e.atrasada],
        }

    @classmethod
    def painel(cls, filial, limite: int = 60) -> list[dict]:
        """
        Uma linha por ordem aberta, com a etapa em que ela está.

        É a tela que o encarregado abre de manhã: o que está na fábrica e
        onde. Ordens encerradas ficam fora — já saíram do chão.
        """
        from apps.moda.models import OrdemProducao

        ordens = (
            OrdemProducao.objects.for_filial(filial)
            .exclude(status__in=OrdemProducao.STATUS_ENCERRADOS)
            .select_related('pedido', 'pedido__cliente', 'item', 'item__produto')
            .prefetch_related('etapas')
            .order_by('prazo', 'numero')[:limite]
        )
        return [{'ordem': o, **cls.resumo(o)} for o in ordens]
