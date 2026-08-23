"""
Indicador de Produção — volume produzido por etapa.

VOLUME SOZINHO NÃO DIZ NADA. "A costura fez 800 peças" só vira informação
ao lado do que ela RECEBEU: 800 de 800 é uma linha saudável, 800 de 900 são
cem peças que sumiram entre uma bancada e outra.

O modelo já resolve essa cadeia: `EtapaOrdem.planejada` herda o produzido da
última etapa concluída antes dela, e é isso que faz a perda do corte chegar
ao planejamento da costura. A tela lê essa cadeia em vez de reinventá-la.

A JANELA É POR DATA DE CONCLUSÃO, e não por abertura da ordem: quem olha
este número quer saber o que SAIU da bancada no período. Ordem aberta em
janeiro e concluída em março produziu em março.
"""
from datetime import timedelta
from decimal import Decimal

from django.shortcuts import render
from django.utils import timezone

from .models import EtapaOrdem
from .views import ModaBaseView

# Etapas que não são bancada de produção: elas marcam passagem
# administrativa e entrariam no relatório como se alguém tivesse costurado.
NAO_PRODUZEM = (
    EtapaOrdem.Etapa.PEDIDO,
    EtapaOrdem.Etapa.PLANEJAMENTO,
    EtapaOrdem.Etapa.MATERIAIS,
)

PERIODOS = (('7', '7 dias'), ('30', '30 dias'), ('90', '90 dias'))


def resumir(etapas) -> list[dict]:
    """
    Uma linha por etapa, na ORDEM DO FLUXO e não por volume.

    Ordenar pela quantidade produzida faria a leitura perder o sentido: o
    que se lê aqui é a peça descendo a fábrica, e a queda entre uma linha e
    a seguinte é justamente o que se procura.
    """
    por_etapa: dict[str, dict] = {}
    for etapa in etapas:
        linha = por_etapa.setdefault(etapa.etapa, {
            'etapa': etapa.etapa,
            'label': etapa.get_etapa_display(),
            'ordens': 0,
            'planejado': 0,
            'produzido': 0,
            'perda': 0,
        })
        linha['ordens'] += 1
        linha['planejado'] += etapa.planejada
        linha['produzido'] += etapa.quantidade_produzida
        linha['perda'] += etapa.perda

    linhas = []
    maior = max((l['produzido'] for l in por_etapa.values()), default=0)
    for valor, label in EtapaOrdem.Etapa.choices:
        if valor in NAO_PRODUZEM or valor not in por_etapa:
            continue
        linha = por_etapa[valor]
        planejado = linha['planejado']
        linha['perdido'] = planejado - linha['produzido']
        linha['percentual_perda'] = (
            (Decimal(linha['perdido']) / planejado * 100).quantize(Decimal('0.1'))
            if planejado else Decimal('0')
        )
        # A barra é relativa à MAIOR etapa, não ao planejado de cada uma:
        # assim o desenho compara as bancadas entre si, que é a leitura.
        linha['barra'] = int(linha['produzido'] / maior * 100) if maior else 0
        linhas.append(linha)
    return linhas


class ProducaoIndicadorView(ModaBaseView):
    """Quanto cada bancada entregou no período, e onde a peça se perdeu."""

    area = 'indicadores'

    def get(self, request):
        dias = (request.GET.get('dias') or '30').strip()
        if dias not in dict(PERIODOS):
            dias = '30'
        desde = timezone.localdate() - timedelta(days=int(dias))

        etapas = list(
            EtapaOrdem.objects
            .filter(
                ordem__filial=request.filial_ativa,
                status=EtapaOrdem.Status.CONCLUIDA,
                data_conclusao__gte=desde,
            )
            .select_related('ordem')
            # `planejada` percorre as irmãs da mesma ordem; sem o prefetch
            # seria uma consulta por etapa lida.
            .prefetch_related('ordem__etapas')
            .order_by('sequencia')
        )

        linhas = resumir(etapas)
        produzido = sum(l['produzido'] for l in linhas)
        perdido = sum(l['perdido'] for l in linhas)

        return render(request, 'moda/producao_indicador.html', {
            'title': 'Produção',
            'linhas': linhas,
            'dias': dias,
            'desde': desde,
            'periodos': PERIODOS,
            'resumo': {
                'etapas': len(linhas),
                'produzido': produzido,
                'perdido': perdido,
                # O gargalo é a bancada que MENOS entregou: é ela que
                # limita o que sai da fábrica, por mais que as outras
                # tenham corrido.
                'menor': min(linhas, key=lambda l: l['produzido']) if linhas else None,
                'pior_perda': (
                    max(linhas, key=lambda l: l['percentual_perda'])
                    if linhas else None
                ),
            },
        })
