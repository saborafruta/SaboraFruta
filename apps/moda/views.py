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
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, Resolver404, resolve, reverse
from django.views import View

from apps.core.services.permissions import (
    PERMISSION_DENIED_MESSAGE, PermissaoRequiredMixin,
)

from .permissoes import AREA_POR_GRUPO, area_da_view, pode_na_area

from .menu import ETAPAS_FLUXO, GRUPOS, GRUPOS_POR_SLUG, buscar_item, total_itens


class ModaBaseView(PermissaoRequiredMixin, View):
    # O acesso ao vertical é barrado antes disto, no FilialMiddleware, que
    # bloqueia /moda/ para empresa cujo segmento não concede o módulo. Aqui
    # a permissão é a de sempre: o que o perfil do usuário pode fazer.
    permissao_modulo = 'moda'
    permissao_acao = 'ver'

    # Área do vertical. Em branco, sai do arquivo da view (ver
    # `permissoes.AREA_POR_MODULO`); declarada aqui, vence a tabela.
    area: str | None = None

    def dispatch(self, request, *args, **kwargs):
        """
        Estreita a permissão do módulo para a permissão da ÁREA.

        O mixin de cima já cobra `moda`; aqui vem a segunda pergunta:
        o cortador pode ver a OP, mas não editar o valor do pedido. Sem
        esta camada, quem entra no vertical faz tudo dentro dele.

        A checagem só roda para usuário logado, e o `super()` cuida do
        anônimo: chamar `tem_permissao` num visitante estouraria antes de
        ele ser mandado para o login.
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

    Filtrar aqui é mais do que estética: sem isto o cortador vê Comercial
    e Financeiro no menu, clica, e leva um 'você não tem permissão'.
    Menu que oferece porta trancada ensina que o sistema é confuso, não
    que a permissão está certa.
    """
    return [
        g for g in GRUPOS
        if pode_na_area(usuario, AREA_POR_GRUPO.get(g.slug))
    ]


def itens_com_tela(grupo) -> set[str]:
    """
    Quais itens do grupo já têm tela de verdade.

    Descoberto por resolução de rota, não por lista mantida à mão: um item
    passa a ter tela quando alguém declara a rota dele em `ROTAS_PRONTAS`, e
    uma segunda lista aqui envelheceria em silêncio — foi o que fez o selo
    "em breve" continuar aparecendo em Clientes e Orçamentos depois de as
    duas telas estarem no ar.
    """
    from .views_apoio import CADASTROS

    prontos = set()
    for item in grupo.itens:
        if item.slug in CADASTROS:
            prontos.add(item.slug)
            continue
        try:
            achado = resolve(reverse('moda:item', args=[grupo.slug, item.slug]))
        except (NoReverseMatch, Resolver404):
            continue
        if getattr(achado.func, 'view_class', None) is not ItemView:
            prontos.add(item.slug)
    return prontos


class ProdutoBuscaView(ModaBaseView):
    """
    Busca de produto por digitação, em JSON — os dois catálogos.

    Fica em `views.py` de propósito: como o hub e os grupos, este arquivo
    não declara área, então quem entra no vertical pode usar. A ficha é do
    PCP e o item do pedido é do comercial, e os dois precisam do MESMO
    campo -- prendê-lo a uma área trancaria a outra para fora.

    NO SERVIDOR, e não filtrando uma lista embutida na página: o catálogo do
    ERP passa de mil itens, e mandá-lo inteiro em toda abertura de
    formulário pesaria em todo mundo para economizar uma consulta rápida.
    """

    def get(self, request):
        from .services.importar_produtos import BuscaProdutos

        produtos = BuscaProdutos.procurar(
            request.filial_ativa,
            termo=request.GET.get('q') or '',
            sem_ficha=request.GET.get('sem_ficha') == '1',
        )
        return JsonResponse({'produtos': produtos})


class HubView(ModaBaseView):
    """Porta de entrada do vertical: os grupos que o perfil enxerga."""

    def get(self, request):
        grupos = grupos_visiveis(request.user)
        return render(request, 'moda/hub.html', {
            'title': 'Moda e Confecção',
            'grupos': grupos,
            'etapas_fluxo': ETAPAS_FLUXO,
            'total_itens': total_itens(),
        })


class GrupoView(ModaBaseView):
    """Telas de um grupo (Comercial, Produtos, Engenharia...)."""

    def get(self, request, grupo_slug):
        grupo = GRUPOS_POR_SLUG.get(grupo_slug)
        if grupo is None:
            raise Http404('Grupo não existe no vertical Moda.')
        # O grupo inteiro é de uma área: quem não tem a área não abre a
        # lista de telas dela, nem por link direto.
        if not pode_na_area(request.user, AREA_POR_GRUPO.get(grupo_slug)):
            messages.error(request, PERMISSION_DENIED_MESSAGE)
            return redirect('moda:hub')
        return render(request, 'moda/grupo.html', {
            'title': grupo.label,
            'grupo': grupo,
            'grupos': grupos_visiveis(request.user),
            'prontos': itens_com_tela(grupo),
        })


class ItemView(ModaBaseView):
    """
    Tela de um item do menu.

    Entrega o cadastro real quando existe um registrado para aquele
    endereço, e a página de "em construção" quando não. Fazer o desvio aqui
    — e não com rotas paralelas — é o que mantém o endereço estável: o link
    do menu é o mesmo antes e depois de a tela ficar pronta.
    """

    def get(self, request, grupo_slug, item_slug):
        grupo, item = buscar_item(grupo_slug, item_slug)
        if grupo is None or item is None:
            raise Http404('Tela não existe no vertical Moda.')
        if not pode_na_area(request.user, AREA_POR_GRUPO.get(grupo_slug)):
            messages.error(request, PERMISSION_DENIED_MESSAGE)
            return redirect('moda:hub')

        from .views_apoio import CADASTROS, CadastroApoioListView

        cadastro = CADASTROS.get(item_slug)
        if cadastro is not None and cadastro.grupo == grupo_slug:
            return CadastroApoioListView.as_view()(request, slug=item_slug)

        return render(request, 'moda/em_construcao.html', {
            'title': item.label,
            'grupo': grupo,
            'item': item,
        })
