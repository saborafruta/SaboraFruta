"""
Alertas automáticos do vertical — o que precisa de alguém agora.

DETECTADOS, NÃO GRAVADOS. As sete condições são todas derivadas do estado
atual: um pedido está atrasado porque a data passou, não porque alguém
gravou "atrasado" um dia. Alerta gravado envelhece — o material chega, o
pedido é entregue, e o aviso continua na tela até alguém limpar à mão. Aqui
a lista é recalculada a cada leitura e, por construção, nunca mente.

O SINO É UMA PROJEÇÃO, e por isso a sincronização tem duas metades: cria as
notificações que apareceram E DESATIVA as que deixaram de valer. Um sistema
de alerta que só acrescenta vira ruído em duas semanas, e ninguém mais olha
— o custo de não desligar é maior que o de não avisar.

LIMIARES SÃO CONSTANTES DESTE MÓDULO, não configuração por filial. É uma
escolha consciente de escopo: um cadastro de limiares exigiria modelo, tela
e migration para um ajuste que ninguém pediu ainda. Estão todos juntos aqui
em cima, com a razão de cada número, e são uma linha para mudar.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notificacao
from apps.moda.models import EtapaOrdem, Inspecao, PedidoProducao, RegistroCorte
from apps.moda.services.necessidade import NecessidadeService
from apps.moda.services.pcp import PcpService

logger = logging.getLogger(__name__)

CEM = Decimal('100')
ZERO = Decimal('0')

# Uma semana: é o horizonte em que ainda dá para reagir a um prazo. Mais
# longe que isso não é urgência, é planejamento — e o PCP já tem tela.
DIAS_VENCIMENTO_PROXIMO = 7

# Abaixo de 95% do planejado a etapa devolveu menos peça do que prometeu.
# 5% de folga porque uma ou duas peças a menos num lote de 200 é ajuste de
# grade, não problema de produção — alertar nisso ensinaria a ignorar.
PRODUCAO_MINIMA = Decimal('95')

# Rejeição (reprovado + retrabalho) acima de 5% da inspeção. É o número que
# a confecção costuma tratar como o limite entre "acontece" e "tem algo
# errado no processo".
REJEICAO_MAXIMA = Decimal('5')

# Abaixo de 75% do tecido virando peça, o risco precisa ser refeito. Acima
# disso a variação é do modelo, não do encaixe.
APROVEITAMENTO_MINIMO = Decimal('75')

# Quantos dias para trás olhar nos alertas que dependem de um registro
# recente (produção, rejeição, corte). Sem janela, um corte ruim de janeiro
# ficaria alertando para sempre.
JANELA_DIAS = 30


@dataclass(frozen=True)
class Regra:
    """Um tipo de alerta: o que é, quão grave, e como vai para o sino."""
    chave: str
    label: str
    severidade: str  # critico | atencao
    tipo: str        # Notificacao.Tipo

    @property
    def critico(self) -> bool:
        return self.severidade == 'critico'


REGRAS = {
    r.chave: r for r in [
        Regra('pedido_atrasado', 'Pedido atrasado', 'critico',
              Notificacao.Tipo.MODA_PEDIDO_ATRASADO),
        Regra('pedido_vencendo', 'Pedido próximo do vencimento', 'atencao',
              Notificacao.Tipo.MODA_PEDIDO_VENCENDO),
        Regra('material_insuficiente', 'Material insuficiente', 'critico',
              Notificacao.Tipo.MODA_MATERIAL_INSUFICIENTE),
        Regra('producao_abaixo', 'Produção abaixo do planejado', 'critico',
              Notificacao.Tipo.MODA_PRODUCAO_ABAIXO),
        Regra('setor_sobrecarregado', 'Máquina/setor sobrecarregado', 'atencao',
              Notificacao.Tipo.MODA_SETOR_SOBRECARREGADO),
        Regra('rejeicao_alta', 'Alto índice de rejeição', 'critico',
              Notificacao.Tipo.MODA_REJEICAO_ALTA),
        Regra('aproveitamento_baixo', 'Baixo aproveitamento de corte', 'atencao',
              Notificacao.Tipo.MODA_APROVEITAMENTO_BAIXO),
        # CRÍTICO, e não "atenção": enquanto ninguém refaz a arte o pedido
        # não anda um passo, e do outro lado há um cliente que já respondeu
        # esperando. Atraso de material a fábrica contorna; este não.
        Regra('cliente_pediu_ajuste', 'Cliente pediu ajuste na arte', 'critico',
              Notificacao.Tipo.MODA_CLIENTE_AJUSTE),
    ]
}


@dataclass
class Alerta:
    regra: Regra
    titulo: str
    mensagem: str
    url: str
    # Identifica O QUE disparou, de forma estável entre execuções. É o que
    # permite ao sino reconhecer o mesmo alerta em vez de criar um novo a
    # cada varredura — e desativar o que sumiu.
    referencia: str

    @property
    def critico(self) -> bool:
        return self.regra.critico


class AlertaService:

    # ── Detecção ─────────────────────────────────────────────────────────

    @classmethod
    def detectar(cls, filial, hoje: date | None = None) -> list[Alerta]:
        """
        Todos os alertas ativos agora, do mais grave para o menos.

        Cada detector é independente e devolve lista: um que não encontre
        nada não impede os outros, e acrescentar o oitavo alerta é uma
        função nova mais uma linha aqui.
        """
        hoje = hoje or timezone.localdate()
        desde = hoje - timedelta(days=JANELA_DIAS)

        alertas = [
            *cls._pedidos_atrasados(filial, hoje),
            *cls._pedidos_vencendo(filial, hoje),
            *cls._materiais_insuficientes(filial),
            *cls._producao_abaixo(filial, desde),
            *cls._setores_sobrecarregados(filial, hoje),
            *cls._rejeicao_alta(filial, desde),
            *cls._aproveitamento_baixo(filial, desde),
            *cls._ajustes_pedidos(filial),
        ]
        # Crítico primeiro: quem abre a tela no meio do turno lê as três
        # primeiras linhas, e elas têm que ser as que param a fábrica.
        alertas.sort(key=lambda a: (not a.critico, a.regra.label))
        return alertas

    @staticmethod
    def resumo(alertas: list[Alerta]) -> dict:
        criticos = [a for a in alertas if a.critico]
        por_regra: dict[str, list[Alerta]] = {}
        for alerta in alertas:
            por_regra.setdefault(alerta.regra.chave, []).append(alerta)
        return {
            'total': len(alertas),
            'criticos': len(criticos),
            'atencao': len(alertas) - len(criticos),
            'por_regra': [
                (REGRAS[chave], lista) for chave, lista in sorted(
                    por_regra.items(),
                    key=lambda x: (not REGRAS[x[0]].critico, REGRAS[x[0]].label),
                )
            ],
        }

    # ── 🔴 Pedido atrasado ───────────────────────────────────────────────

    @staticmethod
    def _pedidos_atrasados(filial, hoje) -> list[Alerta]:
        pedidos = (
            PedidoProducao.objects.for_filial(filial)
            .exclude(status__in=PedidoProducao.STATUS_ENCERRADOS)
            .exclude(status=PedidoProducao.Status.ORCAMENTO)
            .filter(data_prevista_entrega__lt=hoje)
            .select_related('cliente')
        )
        regra = REGRAS['pedido_atrasado']
        return [
            Alerta(
                regra,
                f'Pedido #{p.numero:06d} atrasado',
                f'{p.cliente} · entrega era {p.data_prevista_entrega:%d/%m/%Y} '
                f'({(hoje - p.data_prevista_entrega).days} dias) · '
                f'{p.get_status_display()}',
                reverse('moda:op2-detail', args=[p.pk]) + '#ajustes-cliente',
                f'pedido:{p.pk}',
            )
            for p in pedidos
        ]

    # ── 🟡 Pedido próximo do vencimento ──────────────────────────────────

    @staticmethod
    def _pedidos_vencendo(filial, hoje) -> list[Alerta]:
        limite = hoje + timedelta(days=DIAS_VENCIMENTO_PROXIMO)
        pedidos = (
            PedidoProducao.objects.for_filial(filial)
            .exclude(status__in=PedidoProducao.STATUS_ENCERRADOS)
            .exclude(status=PedidoProducao.Status.ORCAMENTO)
            .filter(data_prevista_entrega__gte=hoje, data_prevista_entrega__lte=limite)
            .select_related('cliente')
        )
        regra = REGRAS['pedido_vencendo']
        alertas = []
        for p in pedidos:
            dias = (p.data_prevista_entrega - hoje).days
            quando = 'hoje' if dias == 0 else f'em {dias} dia' + ('s' if dias > 1 else '')
            alertas.append(Alerta(
                regra,
                f'Pedido #{p.numero:06d} vence {quando}',
                f'{p.cliente} · {p.quantidade_total} peças · {p.get_status_display()}',
                reverse('moda:pedido-detail', args=[p.pk]),
                f'pedido:{p.pk}',
            ))
        return alertas

    # ── 🔴 Material insuficiente ─────────────────────────────────────────

    @staticmethod
    def _materiais_insuficientes(filial) -> list[Alerta]:
        """
        Déficit calculado pelo serviço de necessidade, não recontado aqui.

        Refazer a conta daria dois números para a mesma pergunta na mesma
        tela — e o dia em que divergissem, ninguém saberia qual acreditar.
        """
        regra = REGRAS['material_insuficiente']
        alertas = []
        for linha in NecessidadeService.calcular(filial):
            if not linha.insuficiente:
                continue
            alertas.append(Alerta(
                regra,
                f'Material insuficiente: {linha.descricao}',
                f'previsto {linha.previsto:.2f} · livre {linha.livre:.2f} · '
                f'faltam {linha.deficit:.2f} {linha.unidade}'.replace('.', ','),
                reverse('moda:necessidade'),
                f'material:{linha.chave}',
            ))
        return alertas

    # ── 🔴 Produção abaixo do planejado ──────────────────────────────────

    @staticmethod
    def _producao_abaixo(filial, desde) -> list[Alerta]:
        """
        Etapa concluída que devolveu menos peça do que planejou.

        Só etapas CONCLUÍDAS: uma em andamento com metade da quantidade não
        está abaixo do planejado, está no meio do trabalho — alertar nisso
        dispararia em toda etapa que começou.
        """
        etapas = (
            EtapaOrdem.objects
            .filter(
                ordem__filial=filial,
                status=EtapaOrdem.Status.CONCLUIDA,
                data_conclusao__gte=desde,
            )
            .select_related('ordem', 'ordem__pedido')
        )
        regra = REGRAS['producao_abaixo']
        alertas = []
        for etapa in etapas:
            planejada = etapa.planejada
            if not planejada or not etapa.quantidade_produzida:
                continue
            atingido = Decimal(etapa.quantidade_produzida) / Decimal(planejada) * CEM
            if atingido >= PRODUCAO_MINIMA:
                continue
            falta = planejada - etapa.quantidade_produzida
            alertas.append(Alerta(
                regra,
                f'{etapa.ordem.numero}: {etapa.get_etapa_display()} abaixo do planejado',
                f'planejado {planejada} · produzido {etapa.quantidade_produzida} '
                f'· faltam {falta} peças ({atingido:.0f}% do planejado)',
                reverse('moda:fluxo-ordem', args=[etapa.ordem_id]),
                f'etapa:{etapa.pk}',
            ))
        return alertas

    # ── 🟡 Máquina/setor sobrecarregado ──────────────────────────────────

    @staticmethod
    def _setores_sobrecarregados(filial, hoje) -> list[Alerta]:
        """
        Carga acima da capacidade, semana a semana, pelo PCP.

        A CAPACIDADE DO SISTEMA É POR SETOR, não por máquina: `CapacidadeSetor`
        é o que existe cadastrado. O alerta é do setor e nomeia as máquinas
        mais carregadas dele, que é o mais próximo de "máquina sobrecarregada"
        que dá para afirmar sem inventar um cadastro de capacidade por
        máquina que ninguém preencheu.
        """
        carga = PcpService.carga(filial, hoje=hoje)
        maquinas = {m['maquina']: m for m in carga['maquinas'][:5]}
        regra = REGRAS['setor_sobrecarregado']

        alertas = []
        for linha in carga['linhas']:
            for celula in linha.celulas:
                if not celula.sobrecarga:
                    continue
                detalhe = ''
                if maquinas:
                    detalhe = ' · máquinas mais carregadas: ' + ', '.join(
                        list(maquinas)[:3]
                    )
                alertas.append(Alerta(
                    regra,
                    f'{linha.label} sobrecarregado na semana de '
                    f'{celula.semana:%d/%m}',
                    f'carga {celula.horas:.0f} h contra capacidade de '
                    f'{linha.capacidade / 60:.0f} h ({celula.percentual:.0f}%)'
                    + detalhe,
                    reverse('moda:pcp-capacidade'),
                    f'setor:{linha.setor}:{celula.semana:%Y-%m-%d}',
                ))
                # Um alerta por setor: a semana mais próxima é a que importa,
                # e listar as oito semanas do horizonte encheria a tela com
                # o mesmo problema repetido.
                break
        return alertas

    # ── 🔴 Alto índice de rejeição ───────────────────────────────────────

    @staticmethod
    def _rejeicao_alta(filial, desde) -> list[Alerta]:
        inspecoes = (
            Inspecao.objects.for_filial(filial)
            .filter(data__gte=desde)
            .exclude(quantidade_inspecionada=0)
            .select_related('ordem')
        )
        regra = REGRAS['rejeicao_alta']
        alertas = []
        for inspecao in inspecoes:
            rejeitadas = inspecao.quantidade_reprovada + inspecao.quantidade_retrabalho
            if not rejeitadas:
                continue
            indice = Decimal(rejeitadas) / Decimal(inspecao.quantidade_inspecionada) * CEM
            if indice < REJEICAO_MAXIMA:
                continue
            motivo = (inspecao.motivo or '').strip().replace('\n', ' ')
            alertas.append(Alerta(
                regra,
                f'{inspecao.ordem.numero}: rejeição de {indice:.1f}%'.replace('.', ','),
                f'{rejeitadas} de {inspecao.quantidade_inspecionada} peças '
                f'reprovadas ou em retrabalho'
                + (f' · {motivo[:120]}' if motivo else ''),
                reverse('moda:inspecao-detail', args=[inspecao.pk]),
                f'inspecao:{inspecao.pk}',
            ))
        return alertas

    # ── 🟡 Baixo aproveitamento de corte ─────────────────────────────────

    @staticmethod
    def _aproveitamento_baixo(filial, desde) -> list[Alerta]:
        cortes = (
            RegistroCorte.objects.for_filial(filial)
            .filter(status=RegistroCorte.Status.CORTADO, data__gte=desde)
            .select_related('ordem', 'encaixe', 'tecido')
        )
        regra = REGRAS['aproveitamento_baixo']
        alertas = []
        for corte in cortes:
            aproveitamento = corte.aproveitamento_efetivo
            # Zero significa "não medido", não "aproveitamento nulo". Alertar
            # em corte sem medição transformaria falta de cadastro em
            # problema de produção.
            if aproveitamento <= 0 or aproveitamento >= APROVEITAMENTO_MINIMO:
                continue
            alertas.append(Alerta(
                regra,
                f'Corte #{corte.numero:04d}: aproveitamento de '
                f'{aproveitamento:.1f}%'.replace('.', ','),
                f'{corte.ordem.numero} · perda de {corte.perda_percentual:.1f}% '
                f'({corte.perda_metros:.1f} m de tecido)'.replace('.', ','),
                reverse('moda:corte-detail', args=[corte.pk]),
                f'corte:{corte.pk}',
            ))
        return alertas

    # ── 🔴 Cliente pediu ajuste na arte ──────────────────────────────────

    @staticmethod
    def _ajustes_pedidos(filial, pedido=None) -> list[Alerta]:
        """
        Pedidos cuja última palavra do cliente foi "ajuste".

        É CONDIÇÃO, não evento: vale enquanto a resposta gravada for ajuste, e
        para de valer sozinha quando o cliente aprova a arte nova. Por isso o
        alerta some do sino sem ninguém clicar em nada — que é a regra deste
        módulo inteiro.

        `pedido` restringe a varredura a um só, para o caminho que publica o
        alerta na hora em que o cliente responde, sem varrer a filial toda.
        """
        from apps.moda.models import AprovacaoPedido

        pedidos = (
            PedidoProducao.objects.for_filial(filial)
            .exclude(status__in=PedidoProducao.STATUS_ENCERRADOS)
            .filter(aprovacao__resposta=AprovacaoPedido.Resposta.AJUSTE)
            .select_related('cliente', 'aprovacao')
        )
        if pedido is not None:
            pedidos = pedidos.filter(pk=pedido.pk)

        regra = REGRAS['cliente_pediu_ajuste']
        alertas = []
        for p in pedidos:
            motivo = (p.aprovacao.motivo_ajuste or '').strip()
            # O MOTIVO VAI NA NOTIFICAÇÃO, não só um "tem ajuste". Quem lê o
            # sino no meio do turno decide dali se para o que está fazendo, e
            # "trocar a cor da gola" e "refazer a arte toda" não pedem a mesma
            # coisa. Sem o texto, todo alerta obriga a abrir o pedido.
            quem = p.aprovacao.respondido_por or str(p.cliente)
            alertas.append(Alerta(
                regra,
                f'Pedido #{p.numero:06d}: cliente pediu ajuste',
                f'{quem} · {motivo}' if motivo else f'{quem} · sem motivo informado',
                reverse('moda:pedido-detail', args=[p.pk]),
                f'pedido:{p.pk}',
            ))
        return alertas

    # ── Sincronização com o sino ─────────────────────────────────────────

    @classmethod
    def sincronizar(cls, filial, hoje: date | None = None) -> dict:
        """
        Espelha os alertas no sino: cria os novos e DESLIGA os que passaram.

        A segunda metade é a que importa. Um sistema que só acrescenta vira
        ruído em duas semanas, e a partir daí ninguém mais abre o sino — o
        custo de não desligar é maior que o de não avisar.

        `update_or_create` por (tipo, referência) para o mesmo problema não
        virar cinquenta notificações ao longo de cinquenta varreduras.
        """
        alertas = cls.detectar(filial, hoje)
        criados, atualizados, vistos = cls.publicar(filial, alertas)

        # O que estava ativo e não apareceu nesta varredura: a condição
        # deixou de valer. Desativa em vez de apagar — o histórico de que o
        # alerta existiu continua consultável.
        obsoletas = (
            Notificacao.objects
            .filter(filial=filial, referencia_tipo='moda_alerta', ativa=True)
            .exclude(
                referencia_id__in=[r for _t, r in vistos],
            )
        )
        desligados = obsoletas.update(ativa=False)

        return {
            'detectados': len(alertas),
            'criados': criados,
            'atualizados': atualizados,
            'desligados': desligados,
        }

    @staticmethod
    def publicar(filial, alertas: list[Alerta]) -> tuple[int, int, set]:
        """
        Põe estes alertas no sino. Metade de cima da sincronização.

        Separada porque tem DOIS chamadores com ritmos diferentes: a varredura
        de hora em hora, que publica tudo e desliga o resto, e o evento, que
        publica UM na hora em que acontece e não pode desligar nada — ele não
        varreu a filial, então não sabe o que deixou de valer.

        Compõem sem conflito porque a chave é a mesma: o evento cria, e a
        varredura seguinte reconhece a mesma linha em vez de duplicar.
        """
        criados = atualizados = 0
        vistos = set()
        for alerta in alertas:
            referencia = f'{alerta.regra.chave}:{alerta.referencia}'
            _obj, novo = Notificacao.objects.update_or_create(
                filial=filial,
                tipo=alerta.regra.tipo,
                referencia_tipo='moda_alerta',
                referencia_id=referencia,
                defaults={
                    'titulo': alerta.titulo,
                    'mensagem': alerta.mensagem[:500],
                    'url': alerta.url,
                    'ativa': True,
                },
            )
            vistos.add((alerta.regra.tipo, referencia))
            criados += 1 if novo else 0
            atualizados += 0 if novo else 1
        return criados, atualizados, vistos

    @classmethod
    def avisar_ajuste_do_cliente(cls, pedido) -> None:
        """
        Toca o sino na hora em que o cliente pede ajuste.

        A varredura sozinha não serve aqui: ela roda de hora em hora, e o pedido
        de ajuste é justamente o aviso que não pode esperar a próxima volta —
        há um cliente parado esperando arte nova.

        Reaproveita o detector em vez de montar a notificação à mão. Se o alerta
        fosse escrito duas vezes, um dia o título mudaria num lugar só e o
        sino passaria a mostrar duas versões do mesmo aviso.

        NÃO ESTOURA. Isto roda no POST do cliente, na página pública: falhar
        aqui viraria tela de erro para quem está do lado de fora por causa do
        sino de quem está do lado de dentro. A resposta já está gravada, e a
        varredura seguinte pega o que faltou.
        """
        try:
            cls.publicar(pedido.filial, cls._ajustes_pedidos(pedido.filial, pedido))
        except Exception:  # noqa: BLE001
            logger.exception(
                'Falha ao publicar o alerta de ajuste do pedido %s', pedido.pk,
            )

    @staticmethod
    def encerrar_ajuste_do_cliente(pedido) -> int:
        """
        Desliga na hora o alerta de ajuste deste pedido. Devolve quantos.

        A condição deixou de valer quando a arte foi reenviada: não há mais
        resposta de ajuste, há um cliente olhando a versão nova. A varredura
        desligaria sozinha na próxima volta, mas até lá o sino apontaria para
        um trabalho já feito — e alerta que continua aceso depois de resolvido
        é exatamente o que ensina a ignorar o sino.

        Desativa em vez de apagar, como a varredura: o histórico de que o
        alerta existiu continua consultável.
        """
        regra = REGRAS['cliente_pediu_ajuste']
        return (
            Notificacao.objects
            .filter(
                filial=pedido.filial,
                tipo=regra.tipo,
                referencia_tipo='moda_alerta',
                referencia_id=f'{regra.chave}:pedido:{pedido.pk}',
                ativa=True,
            )
            .update(ativa=False)
        )
