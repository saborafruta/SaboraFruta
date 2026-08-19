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

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.core.models import Notificacao
from apps.moda.models import EtapaOrdem, Inspecao, PedidoProducao, RegistroCorte
from apps.moda.services.necessidade import NecessidadeService
from apps.moda.services.pcp import PcpService

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
                reverse('moda:pedido-detail', args=[p.pk]),
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
        vistos = set()

        criados = atualizados = 0
        for alerta in alertas:
            _obj, novo = Notificacao.objects.update_or_create(
                filial=filial,
                tipo=alerta.regra.tipo,
                referencia_tipo='moda_alerta',
                referencia_id=f'{alerta.regra.chave}:{alerta.referencia}',
                defaults={
                    'titulo': alerta.titulo,
                    'mensagem': alerta.mensagem[:500],
                    'url': alerta.url,
                    'ativa': True,
                },
            )
            vistos.add((alerta.regra.tipo, f'{alerta.regra.chave}:{alerta.referencia}'))
            criados += 1 if novo else 0
            atualizados += 0 if novo else 1

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
