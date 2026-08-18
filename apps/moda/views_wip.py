"""
Painel de WIP (grupo Relatórios).

Só leitura: o apontamento que alimenta estes números é feito no fluxo de
cada ordem. Um painel que também deixasse editar teria duas telas gravando
a mesma etapa, e a que fosse salva por último ganharia sem ninguém saber.
"""
from django.shortcuts import render

from .services.wip import WipService
from .views import ModaBaseView


class WipView(ModaBaseView):
    def get(self, request):
        filtros = {
            'ordem': (request.GET.get('ordem') or '').strip(),
            'cliente': (request.GET.get('cliente') or '').strip(),
            'produto': (request.GET.get('produto') or '').strip(),
            'setor': (request.GET.get('setor') or '').strip(),
            'responsavel': (request.GET.get('responsavel') or '').strip(),
            'de': WipService.data(request.GET.get('de')),
            'ate': WipService.data(request.GET.get('ate')),
        }

        dados = WipService.painel(request.filial_ativa, filtros)
        return render(request, 'moda/wip.html', {
            'title': 'WIP — Trabalho em Processo',
            'filtros': filtros,
            # A string crua volta para o input de data: o valor convertido é
            # `date`, e o `<input type="date">` precisa de texto ISO.
            'de_texto': (request.GET.get('de') or '').strip(),
            'ate_texto': (request.GET.get('ate') or '').strip(),
            'setores': WipService.opcoes_setor(),
            'tem_filtro': any(filtros.values()),
            **dados,
        })
