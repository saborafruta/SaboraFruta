"""
O painel industrial: cinco perguntas numa tela.

E' TELA DE LEITURA, e por isso nao tem nenhuma acao: quem abre o painel esta'
decidindo, nao operando. Botao de agir aqui levaria a mexer na producao a
partir de um numero agregado, sem ver a ordem que ele resume.
"""
from django.shortcuts import render

from .services import IndicadoresService
from .services.rendimento import RendimentoService
from .views import PolpaBaseView

# As janelas que a fabrica usa: a semana (o que aconteceu), o mes (o que
# fechou) e o trimestre (a tendencia). Mais opcoes so' dariam mais lugares
# para o numero mudar sem ninguem entender por que.
JANELAS = (7, 30, 90)


class PainelView(PolpaBaseView):
    """Producao, eficiencia, custo, estoque e qualidade."""

    area = 'indicadores'

    def get(self, request):
        try:
            dias = int(request.GET.get('dias') or 30)
        except ValueError:
            dias = 30
        if dias not in JANELAS:
            dias = 30

        dados = IndicadoresService.painel(request.filial_ativa, dias)
        producao = dados['producao']

        return render(request, 'polpa/painel.html', {
            'title': 'Painel industrial',
            'dados': dados,
            'janelas': JANELAS,
            # OS TRES PERIODOS COM O ROTULO ja' montados: a tela desenha os
            # tres num laco so', e "diaria/semanal/mensal" fica escrito uma
            # vez -- no template seriam tres blocos iguais.
            'periodos': [
                (producao['dia'], 'Hoje'),
                (producao['semana'], 'Semana'),
                (producao['mes'], 'Mes'),
            ],
        })


class RendimentoRealView(PolpaBaseView):
    """
    O que a fruta rendeu contra o que deveria render.

    TELA DE LEITURA, como o painel: quem abre está decidindo o que
    investigar, não operando. O caminho para agir é a ordem — e cada linha
    leva até ela.
    """

    area = 'indicadores'

    def get(self, request):
        try:
            dias = int(request.GET.get('dias') or RendimentoService.JANELA)
        except ValueError:
            dias = RendimentoService.JANELA
        if dias not in JANELAS:
            dias = RendimentoService.JANELA

        return render(request, 'polpa/rendimento_real.html', {
            'title': 'Rendimento real',
            'janelas': JANELAS,
            **RendimentoService.painel(request.filial_ativa, dias),
        })
