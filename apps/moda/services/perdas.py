"""
Perdas — refugo, retrabalho e sobra de tecido.

SÃO TRÊS COISAS DIFERENTES, EM DUAS UNIDADES, e a tela nunca as soma:

  REFUGO, em peças. A peça morreu: virou pano. É perda de verdade.
  RETRABALHO, em peças. A peça voltou para a linha e ainda vai ser
      vendida — não é perda, é custo. Somá-la ao refugo faria a fábrica
      parecer muito pior do que é, e o modelo de qualidade já separa os
      dois status justamente por isso.
  TECIDO, em metros. Não tem como somar com peça, e converter uma na
      outra exigiria um custo que nem toda ficha tem preenchido.

Um número só juntando tudo seria mais bonito e não serviria para decidir
nada: refugo se ataca na bancada onde ele acontece, retrabalho se ataca no
ponto do checklist que reprova, e sobra de tecido se ataca no encaixe. Três
ações diferentes, três blocos na tela.

CUIDADO COM A CONTAGEM DUPLA. `QualidadeService.aplicar_no_fluxo` grava
`quantidade_reprovada` da inspeção dentro de `EtapaOrdem.perda` na etapa de
Qualidade. Somar a inspeção por cima da etapa contaria o mesmo refugo duas
vezes. Por isso o refugo em peças sai SÓ das etapas, e a inspeção entra
aqui por outro lado: a causa (o ponto do checklist que reprovou) e o
retrabalho, que nunca chega às etapas.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from ..models import EtapaOrdem, Inspecao, ItemInspecao, RegistroCorte

ZERO = Decimal('0')
CEM = Decimal('100')

# As mesmas etapas administrativas que o indicador de produção ignora: elas
# não são bancada, e uma perda apontada ali seria erro de digitação.
NAO_PRODUZEM = (
    EtapaOrdem.Etapa.PEDIDO,
    EtapaOrdem.Etapa.PLANEJAMENTO,
    EtapaOrdem.Etapa.MATERIAIS,
)

PERIODOS = (('7', '7 dias'), ('30', '30 dias'), ('90', '90 dias'))


def _pct(numerador, denominador):
    """Percentual, ou None quando não há base — que NÃO é o mesmo que 0%."""
    if not denominador:
        return None
    return (Decimal(numerador) / Decimal(denominador) * CEM).quantize(Decimal('0.1'))


def _barra(valor, maior) -> int:
    return int(Decimal(valor) / Decimal(maior) * CEM) if maior else 0


class PerdasService:
    """Os três blocos, cada um na unidade dele."""

    @classmethod
    def painel(cls, filial, dias: int) -> dict:
        desde = timezone.localdate() - timedelta(days=dias)
        return {
            'desde': desde,
            'refugo': cls.refugo(filial, desde),
            'retrabalho': cls.retrabalho(filial, desde),
            'causas': cls.causas(filial, desde),
            'tecido': cls.tecido(filial, desde),
        }

    # ── Refugo: peças que morreram, por bancada ──────────────────────────

    @classmethod
    def refugo(cls, filial, desde) -> dict:
        """
        Peças refugadas por etapa — FONTE ÚNICA para o refugo em peças.

        Não somar `Inspecao.quantidade_reprovada` por cima: a inspeção
        aplicada no fluxo já está aqui dentro, na etapa de Qualidade.
        """
        etapas = (
            EtapaOrdem.objects
            .filter(
                ordem__filial=filial,
                status=EtapaOrdem.Status.CONCLUIDA,
                data_conclusao__gte=desde,
            )
            .exclude(etapa__in=NAO_PRODUZEM)
            .select_related('ordem')
        )

        por_etapa: dict[str, dict] = {}
        for etapa in etapas:
            linha = por_etapa.setdefault(etapa.etapa, {
                'etapa': etapa.etapa,
                'label': etapa.get_etapa_display(),
                'perda': 0,
                'produzido': 0,
                'ordens': set(),
            })
            linha['perda'] += etapa.perda
            linha['produzido'] += etapa.quantidade_produzida
            linha['ordens'].add(etapa.ordem_id)

        maior = max((l['perda'] for l in por_etapa.values()), default=0)
        linhas = []
        # Na ordem do fluxo: perder no acabamento é muito mais caro do que
        # perder no corte, porque a peça já consumiu todas as bancadas
        # anteriores. A sequência é o que deixa isso visível.
        for valor, _ in EtapaOrdem.Etapa.choices:
            linha = por_etapa.get(valor)
            if linha is None or not (linha['perda'] or linha['produzido']):
                continue
            # A base é o que PASSOU pela bancada (produzido + perda), e não
            # `planejada`: aquela caminha as etapas irmãs de cada ordem, o
            # que sairia caro, e escorrega quando a etapa não fecha. Aqui a
            # pergunta é simples — do que entrou nesta bancada, quanto
            # morreu nela.
            base = linha['perda'] + linha['produzido']
            linha['ordens'] = len(linha['ordens'])
            linha['percentual'] = _pct(linha['perda'], base)
            linha['barra'] = _barra(linha['perda'], maior)
            linhas.append(linha)

        pecas = sum(l['perda'] for l in linhas)
        produzidas = sum(l['produzido'] for l in linhas)
        com_perda = [l for l in linhas if l['perda']]
        return {
            'linhas': linhas,
            'pecas': pecas,
            'produzidas': produzidas,
            'percentual': _pct(pecas, pecas + produzidas),
            # A pior bancada é a de MAIOR PERCENTUAL, e não a de maior
            # número: o setor que passa mil peças e perde dez está melhor
            # do que o que passa vinte e perde cinco.
            'pior': max(com_perda, key=lambda l: l['percentual'], default=None),
        }

    # ── Retrabalho: peças que voltaram, e não morreram ───────────────────

    @staticmethod
    def retrabalho(filial, desde) -> dict:
        """
        O que a qualidade mandou de volta para a linha.

        Fica fora do refugo de propósito — a peça ainda vai ser vendida.
        Mas fica NA TELA porque custa: é bancada refazendo o que já tinha
        feito, e some do custo se ninguém contar.
        """
        inspecoes = list(
            Inspecao.objects.for_filial(filial)
            .filter(data__gte=desde)
            .exclude(status=Inspecao.Status.EM_ANDAMENTO)
        )
        pecas = sum(i.quantidade_retrabalho for i in inspecoes)
        inspecionadas = sum(i.quantidade_inspecionada for i in inspecoes)
        return {
            'pecas': pecas,
            'inspecoes': len(inspecoes),
            'com_retrabalho': sum(1 for i in inspecoes if i.quantidade_retrabalho),
            'inspecionadas': inspecionadas,
            'percentual': _pct(pecas, inspecionadas),
        }

    # ── Causas: onde o checklist reprova ─────────────────────────────────

    @staticmethod
    def causas(filial, desde) -> list[dict]:
        """
        Os pontos do checklist que mais reprovaram.

        É o único bloco que diz o que FAZER: refugo por etapa mostra onde a
        peça morre, e isto mostra por quê. O checklist é fixo justamente
        para que essa contagem seja comparável de um mês para o outro.
        """
        itens = (
            ItemInspecao.objects
            .filter(
                inspecao__filial=filial,
                inspecao__data__gte=desde,
                resultado=ItemInspecao.Resultado.NAO_CONFORME,
            )
            .values_list('ponto', flat=True)
        )
        contagem: dict[str, int] = {}
        for ponto in itens:
            contagem[ponto] = contagem.get(ponto, 0) + 1

        maior = max(contagem.values(), default=0)
        # Ordenado por FREQUÊNCIA, e não pela ordem do checklist: aqui a
        # pergunta é "o que atacar primeiro", e a resposta é o topo da lista.
        return [
            {
                'ponto': ponto,
                'label': ItemInspecao.Ponto(ponto).label,
                'ocorrencias': quantas,
                'barra': _barra(quantas, maior),
            }
            for ponto, quantas in sorted(
                contagem.items(), key=lambda par: (-par[1], par[0]),
            )
        ]

    # ── Tecido: metros que não viraram peça ──────────────────────────────

    @classmethod
    def tecido(cls, filial, desde) -> dict:
        """
        Sobra de tecido por tecido, em metros.

        Duas perdas diferentes convivem aqui, e o modelo de corte já avisa
        para não confundi-las: APROVEITAMENTO é quanto do tecido virou peça
        no risco, e VARIAÇÃO é o enfesto ter gasto mais do que a ficha
        previa. Uma se conserta no encaixe, a outra na mesa de corte.
        """
        cortes = list(
            RegistroCorte.objects.for_filial(filial)
            .filter(status=RegistroCorte.Status.CORTADO, data__gte=desde)
            .select_related('tecido', 'encaixe', 'ordem__item__tecido',
                            'ordem__item__produto__tecido')
            .prefetch_related('ordem__item__produto__ficha__materiais')
        )

        por_tecido: dict = {}
        for corte in cortes:
            tecido = corte.tecido_efetivo
            chave = tecido.pk if tecido else 0
            linha = por_tecido.setdefault(chave, {
                'label': tecido.nome if tecido else 'Sem tecido informado',
                'cortes': 0,
                'consumo': ZERO,
                'planejado': ZERO,
                'perda_metros': ZERO,
                'sem_medida': 0,
                'medidos': ZERO,
                'soma_aproveitamento': ZERO,
            })
            linha['cortes'] += 1
            consumo = corte.consumo_real or ZERO
            linha['consumo'] += consumo
            linha['planejado'] += corte.planejado
            linha['perda_metros'] += corte.perda_metros

            aproveitamento = corte.aproveitamento_efetivo
            if aproveitamento > 0:
                # Média PONDERADA pelo tecido gasto: a simples trataria um
                # corte de 2 m igual a um de 200 m, e é o de 200 que decide
                # o custo do mês.
                linha['medidos'] += consumo
                linha['soma_aproveitamento'] += aproveitamento * consumo
            else:
                # Zero ali significa "ninguém mediu", não "perdeu tudo".
                linha['sem_medida'] += 1

        linhas = cls._fechar_tecido(por_tecido)
        metros = sum((l['perda_metros'] for l in linhas), ZERO)
        gasto = sum((l['consumo'] for l in linhas), ZERO)
        planejado = sum((l['planejado'] for l in linhas), ZERO)
        medidos = sum((l['medidos'] for l in linhas), ZERO)
        ponderado = sum((l['soma_aproveitamento'] for l in linhas), ZERO)
        return {
            'linhas': linhas,
            'metros': metros.quantize(Decimal('0.01')),
            'consumo': gasto.quantize(Decimal('0.01')),
            'planejado': planejado.quantize(Decimal('0.01')),
            'variacao': (gasto - planejado).quantize(Decimal('0.01')),
            'aproveitamento': (
                (ponderado / medidos).quantize(Decimal('0.1')) if medidos else None
            ),
            'cortes': sum(l['cortes'] for l in linhas),
            'sem_medida': sum(l['sem_medida'] for l in linhas),
            'pior': max(
                (l for l in linhas if l['perda_metros']),
                key=lambda l: l['perda_metros'], default=None,
            ),
        }

    @staticmethod
    def _fechar_tecido(por_tecido) -> list[dict]:
        maior = max((l['perda_metros'] for l in por_tecido.values()), default=ZERO)
        linhas = []
        for linha in por_tecido.values():
            medidos = linha['medidos']
            linha['aproveitamento'] = (
                (linha['soma_aproveitamento'] / medidos).quantize(Decimal('0.1'))
                if medidos else None
            )
            linha['variacao'] = (linha['consumo'] - linha['planejado']).quantize(Decimal('0.01'))
            linha['estourou'] = linha['variacao'] > 0
            linha['perda_metros'] = linha['perda_metros'].quantize(Decimal('0.01'))
            linha['consumo'] = linha['consumo'].quantize(Decimal('0.01'))
            linha['planejado'] = linha['planejado'].quantize(Decimal('0.01'))
            linha['barra'] = _barra(linha['perda_metros'], maior)
            linhas.append(linha)
        # Por metros perdidos, do pior para o melhor: é a fila de quem
        # revisar o encaixe primeiro.
        return sorted(linhas, key=lambda l: l['perda_metros'], reverse=True)
