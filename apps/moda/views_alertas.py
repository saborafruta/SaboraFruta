"""
Tela de alertas do vertical.

Leitura pura: a lista é recalculada a cada visita, e não há nada para
gravar. "Marcar como lido" não existe de propósito — o alerta some quando a
CONDIÇÃO passa (o material chega, o pedido é entregue), não quando alguém
clica. Um botão de dispensar deixaria a tela verde com a fábrica parada.
"""
from django.shortcuts import render

from .services.alertas import REGRAS, AlertaService
from .views import ModaBaseView


class AlertasView(ModaBaseView):

    def get(self, request):
        alertas = AlertaService.detectar(request.filial_ativa)
        filtro = request.GET.get('regra', '')

        visiveis = [a for a in alertas if a.regra.chave == filtro] if filtro in REGRAS else alertas

        return render(request, 'moda/alertas.html', {
            'title': 'Alertas',
            'alertas': visiveis,
            'resumo': AlertaService.resumo(alertas),
            'filtro': filtro if filtro in REGRAS else '',
        })
