"""
Navegação do vertical Polpa de Frutas: hub, grupos e telas em construção.

Enquanto as telas reais não existem, as rotas são atendidas por `ItemView`,
que renderiza uma página nomeando o que vai ali. É deliberado: com o menu
inteiro navegável dá para validar a estrutura do processo — que é o que o
dono da fábrica reconhece ou corrige — antes de construir as telas, e
nenhum link do menu leva a 404.

Quando uma tela fica pronta, a rota dela é declarada em `urls.py` antes do
catch-all. O endereço não muda, então o link do menu continua valendo, e o
selo "em breve" some sozinho: quem responde se a tela existe é a resolução
da rota, não uma segunda lista mantida à mão.
"""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, Resolver404, resolve, reverse
from django.views import View

from apps.core.services.permissions import (
    PERMISSION_DENIED_MESSAGE, PermissaoRequiredMixin,
)

from .menu import ETAPAS_FLUXO, GRUPOS, GRUPOS_POR_SLUG, buscar_item, total_itens
from .permissoes import AREA_POR_GRUPO, area_da_view, pode_na_area


class PolpaBaseView(PermissaoRequiredMixin, View):
    # O acesso ao vertical é barrado antes disto, no FilialMiddleware, que
    # bloqueia /polpa/ para empresa cujo segmento não concede o módulo. Aqui
    # a permissão é a de sempre: o que o perfil do usuário pode fazer.
    permissao_modulo = 'polpa'
    permissao_acao = 'ver'

    # Área do vertical. Em branco, sai do arquivo da view (ver
    # `permissoes.AREA_POR_MODULO`); declarada aqui, vence a tabela.
    area: str | None = None

    def dispatch(self, request, *args, **kwargs):
        """
        Estreita a permissão do módulo para a permissão da ÁREA.

        Quem está na balança às 5h da manhã não é quem libera lote na
        qualidade. Sem esta camada, entrar no vertical daria acesso a tudo
        dentro dele — inclusive aprovar o próprio recebimento.
        """
        if request.user.is_authenticated:
            area = area_da_view(self)
            if area and not pode_na_area(request.user, area, self.permissao_acao):
                messages.error(request, PERMISSION_DENIED_MESSAGE)
                return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)


def grupos_visiveis(usuario) -> list:
    """
    Os grupos do menu que este perfil pode abrir.

    Menu que oferece porta trancada ensina que o sistema é confuso, não que
    a permissão está certa.
    """
    return [
        g for g in GRUPOS
        if pode_na_area(usuario, AREA_POR_GRUPO.get(g.slug))
    ]


def itens_com_tela(grupo) -> set[str]:
    """
    Quais itens do grupo já têm tela de verdade.

    Descoberto por resolução de rota, e não por lista à mão: uma segunda
    lista envelheceria em silêncio, e o selo "em breve" continuaria
    aparecendo em tela pronta.
    """
    prontos = set()
    for item in grupo.itens:
        try:
            achado = resolve(reverse('polpa:item', args=[grupo.slug, item.slug]))
        except (NoReverseMatch, Resolver404):
            continue
        if getattr(achado.func, 'view_class', None) is not ItemView:
            prontos.add(item.slug)
    return prontos


class HubView(PolpaBaseView):
    """A porta do vertical: o processo inteiro numa tela."""

    def get(self, request):
        from .services import RecebimentoService

        grupos = grupos_visiveis(request.user)
        return render(request, 'polpa/hub.html', {
            'title': 'Polpa de Frutas',
            'grupos': grupos,
            'etapas': ETAPAS_FLUXO,
            'total_itens': total_itens(),
            # O DIA DA BALANÇA já no hub: é a informação que muda de hora em
            # hora e a única que alguém abre o sistema de manhã para ver.
            'resumo': RecebimentoService.resumo(request.filial_ativa),
        })


class GrupoView(PolpaBaseView):
    """As telas de um grupo."""

    def get(self, request, grupo_slug):
        grupo = GRUPOS_POR_SLUG.get(grupo_slug)
        if grupo is None:
            raise Http404('Grupo não encontrado.')

        area = AREA_POR_GRUPO.get(grupo.slug)
        if not pode_na_area(request.user, area):
            messages.error(request, PERMISSION_DENIED_MESSAGE)
            return redirect('polpa:hub')

        return render(request, 'polpa/grupo.html', {
            'title': grupo.label,
            'grupo': grupo,
            'prontos': itens_com_tela(grupo),
        })


class ItemView(PolpaBaseView):
    """
    A tela que ainda não existe — dizendo o que vai existir.

    Nomear o que vem é diferente de um "em breve" vazio: quem abre descobre
    se o que procura está planejado ou se precisa pedir, e o mapa do módulo
    fica visível antes de o módulo estar pronto.
    """

    def get(self, request, grupo_slug, item_slug):
        grupo, item = buscar_item(grupo_slug, item_slug)
        if grupo is None or item is None:
            raise Http404('Tela não encontrada.')

        area = AREA_POR_GRUPO.get(grupo.slug)
        if not pode_na_area(request.user, area):
            messages.error(request, PERMISSION_DENIED_MESSAGE)
            return redirect('polpa:hub')

        return render(request, 'polpa/item.html', {
            'title': item.label,
            'grupo': grupo,
            'item': item,
        })
