"""
Telas do vertical Moda e Confecção.

Enquanto as telas reais não existem, as rotas são atendidas por
`ItemView`, que renderiza uma página de "em construção" nomeando o que
vai ali. É deliberado: com o menu inteiro navegável, dá para validar a
estrutura antes de construir as 62 telas, e nenhum link do menu leva a
um 404.

Quando uma tela fica pronta, o roteamento dela sai do catch-all em
`urls.py` e vira uma rota própria — o item continua no menu, apontando
para o mesmo endereço.
"""
from django.http import Http404
from django.shortcuts import render
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin

from .menu import ETAPAS_FLUXO, GRUPOS, GRUPOS_POR_SLUG, buscar_item, total_itens


class ModaBaseView(PermissaoRequiredMixin, View):
    # O acesso ao vertical é barrado antes disto, no FilialMiddleware, que
    # bloqueia /moda/ para empresa cujo segmento não concede o módulo. Aqui
    # a permissão é a de sempre: o que o perfil do usuário pode fazer.
    permissao_modulo = 'moda'
    permissao_acao = 'ver'


class HubView(ModaBaseView):
    """Porta de entrada do vertical: os 8 grupos do fluxo."""

    def get(self, request):
        return render(request, 'moda/hub.html', {
            'title': 'Moda e Confecção',
            'grupos': GRUPOS,
            'etapas_fluxo': ETAPAS_FLUXO,
            'total_itens': total_itens(),
        })


class GrupoView(ModaBaseView):
    """Telas de um grupo (Comercial, Produtos, Engenharia...)."""

    def get(self, request, grupo_slug):
        grupo = GRUPOS_POR_SLUG.get(grupo_slug)
        if grupo is None:
            raise Http404('Grupo não existe no vertical Moda.')
        return render(request, 'moda/grupo.html', {
            'title': grupo.label,
            'grupo': grupo,
            'grupos': GRUPOS,
        })


class ItemView(ModaBaseView):
    """Placeholder de uma tela ainda não construída."""

    def get(self, request, grupo_slug, item_slug):
        grupo, item = buscar_item(grupo_slug, item_slug)
        if grupo is None or item is None:
            raise Http404('Tela não existe no vertical Moda.')
        return render(request, 'moda/em_construcao.html', {
            'title': item.label,
            'grupo': grupo,
            'item': item,
        })
