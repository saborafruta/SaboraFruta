import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from apps.core.models import Usuario
from apps.core.views.menu_favoritos import MenuFavoritosView, normalizar_caminho_favorito


class MenuFavoritosViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.usuario = SimpleNamespace(pk=7, menu_favoritos=[], save=Mock())

    def _post(self, caminho, favorito, *, autenticado=True, banco_usuario='default'):
        request = self.factory.post(
            reverse('core:menu-favoritos'),
            data=json.dumps({'caminho': caminho, 'favorito': favorito}),
            content_type='application/json',
        )
        request.user = (
            SimpleNamespace(
                is_authenticated=True,
                pk=self.usuario.pk,
                _state=SimpleNamespace(db=banco_usuario),
            )
            if autenticado else AnonymousUser()
        )
        selecionado = Mock()
        selecionado.get.return_value = self.usuario
        manager_banco = Mock()
        manager_banco.select_for_update.return_value = selecionado
        with patch(
            'apps.core.views.menu_favoritos.Usuario.objects.using',
            return_value=manager_banco,
        ) as usar_banco, patch(
            'apps.core.views.menu_favoritos.transaction.atomic',
            return_value=nullcontext(),
        ) as transacao:
            response = MenuFavoritosView.as_view()(request)
        if usar_banco.called:
            usar_banco.assert_called_once_with(banco_usuario)
            transacao.assert_called_once_with(using=banco_usuario)
        return response

    def test_superadmin_salva_favorito_no_banco_da_autenticacao(self):
        response = self._post(
            '/financeiro/posicao-diaria/',
            True,
            banco_usuario='default',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.usuario.menu_favoritos, ['/financeiro/posicao-diaria/'])
        self.usuario.save.assert_called_once_with(
            using='default',
            update_fields=['menu_favoritos', 'updated_at'],
        )

    def test_usuario_tenant_salva_favorito_no_proprio_banco(self):
        response = self._post(
            '/financeiro/posicao-diaria/',
            True,
            banco_usuario='empresa_eureka_50649395000126',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.usuario.menu_favoritos, ['/financeiro/posicao-diaria/'])
        self.usuario.save.assert_called_once_with(
            using='empresa_eureka_50649395000126',
            update_fields=['menu_favoritos', 'updated_at'],
        )

    def test_adiciona_e_remove_favorito(self):
        response = self._post('/financeiro/pagar/?status=pendente', True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.usuario.menu_favoritos, ['/financeiro/pagar/'])

        response = self._post('/financeiro/pagar/', False)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.usuario.menu_favoritos, [])
        self.assertEqual(self.usuario.save.call_count, 2)

    def test_atualiza_somente_usuario_selecionado(self):
        outro_usuario = SimpleNamespace(menu_favoritos=['/core/dashboard/'])

        self._post('/estoque/ajuste-rapido/', True)

        self.assertEqual(self.usuario.menu_favoritos, ['/estoque/ajuste-rapido/'])
        self.assertEqual(outro_usuario.menu_favoritos, ['/core/dashboard/'])

    def test_rejeita_url_externa_e_logout(self):
        externo = self._post('https://exemplo.com/financeiro/', True)
        logout = self._post('/auth/logout/', True)

        self.assertEqual(externo.status_code, 400)
        self.assertEqual(logout.status_code, 400)
        self.usuario.save.assert_not_called()

    def test_exige_autenticacao(self):
        response = self._post('/financeiro/', True, autenticado=False)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertFalse(json.loads(response.content)['ok'])
        self.usuario.save.assert_not_called()

    def test_normaliza_query_e_rejeita_caminho_relativo(self):
        self.assertEqual(
            normalizar_caminho_favorito('/financeiro/pagar/?status=pendente'),
            '/financeiro/pagar/',
        )
        self.assertIsNone(normalizar_caminho_favorito('financeiro/pagar/'))

    def test_modelo_inicia_sem_favoritos(self):
        self.assertEqual(Usuario().menu_favoritos, [])

    def test_get_resolve_submenus_e_preserva_ordem_sem_expor_links_ausentes(self):
        request = self.factory.get(reverse('core:menu-favoritos'))
        request.user = SimpleNamespace(is_authenticated=True, menu_favoritos=[
            '/financeiro/pagar/', '/estoque/ajuste-rapido/', '/restrito/',
        ])
        html = ('<a href="/estoque/ajuste-rapido/"><span>Ajuste rápido</span></a>'
                '<a href="/financeiro/pagar/" title="Contas a pagar">Pagar</a>'
                '<a href="https://externo.test/restrito/">Externo</a>')
        with patch('apps.core.views.menu_favoritos.render_to_string', return_value=html) as render:
            response = MenuFavoritosView.as_view()(request)
        render.assert_called_once_with('core/_sidebar.html', request=request)
        self.assertEqual(json.loads(response.content)['itens'], [
            {'caminho': '/financeiro/pagar/', 'nome': 'Contas a pagar'},
            {'caminho': '/estoque/ajuste-rapido/', 'nome': 'Ajuste rápido'},
        ])


class MenuFavoritosTemplateTests(SimpleTestCase):
    def test_sidebar_carrega_favoritos_no_desktop_e_celular(self):
        raiz = Path(__file__).resolve().parents[1]
        template = (raiz / 'templates' / 'core' / '_sidebar.html').read_text(encoding='utf-8')
        script = (raiz / 'static' / 'core' / 'js' / 'sidebar_favorites.js').read_text(encoding='utf-8')

        self.assertEqual(template.count('sidebar-favorites-nav'), 2)
        self.assertIn('core/js/sidebar_favorites.js', template)
        self.assertIn('request.user.menu_favoritos', template)
        self.assertIn('@media (hover: hover) and (pointer: fine)', template)
        self.assertIn('.sidebar-favorite-toggle:not(.is-favorite)', template)
        self.assertIn('.sidebar-mobile .sidebar-favorite-toggle:not(.is-favorite)', template)
        self.assertIn('.sidebar-mobile .sidebar-favorite-toggle.is-favorite', template)
        self.assertIn('class="sidebar-mobile fixed inset-y-0', template)
        self.assertIn("record.nav.closest('.sidebar-mobile')", script)
        self.assertIn('if (!record.mobileReadonly)', script)
        self.assertIn("'Accept': 'application/json'", script)
        self.assertIn('readJsonResponse', script)
        self.assertEqual(template.count('data-sidebar-dashboard'), 2)  # link + regra de alinhamento
        self.assertIn('class="flex items-center gap-3 px-3 py-2.5', template)
        self.assertNotIn('sidebar-home-link.is-active', template)
        self.assertIn('.sidebar-home-label {', template)
        self.assertIn('<span class="sidebar-home-label">{{ pagina_inicial_nome }}</span>', template)
        home_label_rule = template.split('.sidebar-home-label {', 1)[1].split('}', 1)[0]
        self.assertIn('font-family: Inter, ui-sans-serif, system-ui, sans-serif;', home_label_rule)
        self.assertIn('font-size: 13px;', home_label_rule)
        self.assertIn('font-weight: 600;', home_label_rule)
        self.assertIn('letter-spacing: 0.1em;', home_label_rule)
        self.assertIn('text-transform: uppercase;', home_label_rule)
        self.assertIn('aria-current="page"', template)
        self.assertIn("nav.querySelector('[data-sidebar-search]')", script)
        self.assertIn("search.insertAdjacentElement('afterend', panel)", script)
        self.assertNotIn('nav.appendChild(panel)', script)
        self.assertIn("localStorage.getItem('sidebar-favorites-collapsed')", script)
        self.assertIn("localStorage.setItem(\n          'sidebar-favorites-collapsed'", script)
        self.assertIn("heading.setAttribute('aria-expanded'", script)
        self.assertIn('list.hidden = favoritesCollapsed', script)
        self.assertIn("heading.addEventListener('click'", script)
        self.assertIn('link.appendChild(favoriteIcon(record))', script)
        self.assertIn('.sidebar-favorites-list[hidden]', template)
        self.assertIn('.sidebar-favorites-chevron', template)
        self.assertIn('.sidebar-favorites-panel.is-collapsed {', template)
        self.assertIn('border: 1px solid rgba(245, 158, 11, 0.28);', template)
        self.assertIn('background: rgba(245, 158, 11, 0.08);', template)

    def test_sanfonas_restauram_estado_expandido_e_visual_simples(self):
        raiz = Path(__file__).resolve().parents[1]
        sidebar = (raiz / 'templates' / 'core' / '_sidebar.html').read_text(encoding='utf-8')
        navegacao = (raiz / 'templates' / 'core' / '_sidebar_navigation.html').read_text(encoding='utf-8')

        estado_aberto = (
            'var padrao = {cadastros:true, operacoes:true, financeiro:true, '
            'logistica:true, avancado:true, food_service:true, moda:true, polpa:true};'
        )
        self.assertIn(estado_aberto, sidebar)
        self.assertIn("localStorage.getItem('sidebar-secoes')", sidebar)
        self.assertIn("localStorage.setItem('sidebar-secoes'", sidebar)
        self.assertIn('window.aplicarFocoTelaAtualSidebar', sidebar)
        self.assertIn('window.sincronizarSecoesSidebar', sidebar)
        self.assertIn('window.expandirSecaoAtualSidebar', sidebar)
        self.assertIn('.sidebar-group-current-only > a:not(.sidebar-current-entry)', sidebar)
        self.assertIn("melhorLink.setAttribute('aria-current', 'page')", sidebar)
        self.assertIn('window.aplicarFocoTelaAtualSidebar($root)', sidebar)
        self.assertIn('window.sincronizarSecoesSidebar(secoes, secaoAtual)', sidebar)
        for nome_secao in (
            'cadastros', 'operacoes', 'financeiro', 'logistica',
            'avancado', 'food_service', 'moda', 'polpa',
        ):
            marcador = f'data-sidebar-page-group="{nome_secao}"'
            self.assertEqual(sidebar.count(marcador), 1)
            self.assertEqual(navegacao.count(marcador), 1)
        self.assertEqual(sidebar.count('window.expandirSecaoAtualSidebar($root,'), 8)
        self.assertEqual(navegacao.count('window.expandirSecaoAtualSidebar($root,'), 8)
        self.assertEqual(sidebar.count('sidebar-module-button'), 9)  # 1 regra tipografica + 8 botoes mobile
        self.assertEqual(navegacao.count('sidebar-module-button'), 8)
        self.assertIn('class="flex items-center gap-3 px-3 py-2.5', navegacao)
        self.assertIn(
            '<span class="sidebar-home-label" x-show="!collapsed">{{ pagina_inicial_nome }}</span>',
            navegacao,
        )
        self.assertIn('aria-current="page"', navegacao)
        self.assertIn('data-sidebar-dashboard\n         class="flex items-center', navegacao)
        self.assertIn('.sidebar-module-button > .sidebar-module-label', sidebar)
        self.assertIn('.sidebar-module-icon {', sidebar)
        self.assertIn('gap: 12px;', sidebar)
        self.assertIn(
            'aside nav > a[data-sidebar-dashboard] > svg:first-child',
            sidebar,
        )
        dashboard_icon_rule = sidebar.split(
            'aside nav > a[data-sidebar-dashboard] > svg:first-child {', 1
        )[1].split('}', 1)[0]
        self.assertIn('width: 18px !important;', dashboard_icon_rule)
        self.assertIn('padding: 0;', dashboard_icon_rule)
        self.assertIn('background: transparent !important;', dashboard_icon_rule)
        self.assertIn('box-shadow: none !important;', dashboard_icon_rule)
        self.assertEqual(
            sidebar.count('include "core/_sidebar_module_icon.html"'),
            8,
        )
        self.assertEqual(
            navegacao.count('include "core/_sidebar_module_icon.html"'),
            8,
        )
        self.assertIn('color: #1d4ed8 !important;', sidebar)
        self.assertNotIn('border: 1px solid #d7e2f2 !important;', sidebar)
        self.assertNotIn('inset 3px 0 0 #2563eb', sidebar)
        self.assertNotIn('border: 1px solid #fed7aa !important;', sidebar)
        self.assertIn('font-family: Inter, ui-sans-serif, system-ui, sans-serif !important;', sidebar)
        self.assertIn('font-size: 13px !important;', sidebar)
        self.assertIn('font-weight: 600 !important;', sidebar)
        for base_relativo in (
            Path('..') / 'templates' / '_base.html',
            Path('..') / '..' / 'templates' / '_base.html',
        ):
            base = (raiz / base_relativo).resolve().read_text(encoding='utf-8')
            regra_tema_claro = base.split(
                'body.tema-claro .sidebar-section-label {', 1
            )[1].split('}', 1)[0]
            self.assertIn('color: #2563eb !important;', regra_tema_claro)
            self.assertNotIn('font-family:', regra_tema_claro)
            self.assertNotIn('font-size:', regra_tema_claro)
            self.assertNotIn('font-weight:', regra_tema_claro)
            self.assertNotIn('letter-spacing:', regra_tema_claro)
        self.assertNotIn('repeating-linear-gradient(135deg', sidebar)
        self.assertNotIn('.sidebar-module-button:hover', sidebar)

    def test_produtos_principais_nao_ficam_ativos_dentro_de_moda(self):
        raiz = Path(__file__).resolve().parents[1]
        template = (raiz / 'templates' / 'core' / '_sidebar.html').read_text(encoding='utf-8')
        template += (raiz / 'templates' / 'core' / '_sidebar_navigation.html').read_text(encoding='utf-8')

        self.assertNotIn('produtos_url in request.path', template)
        self.assertEqual(
            template.count("request.resolver_match.namespace == 'produtos'"),
            2,
        )

    def test_verticais_do_menu_mobile_nao_herdam_sidebar_recolhida(self):
        raiz = Path(__file__).resolve().parents[1]
        template = (raiz / 'templates' / 'core' / '_sidebar.html').read_text(encoding='utf-8')
        drawer_mobile = template.split('<!-- DRAWER MOBILE -->', 1)[1]

        self.assertIn(
            "@click=\"window.expandirSecaoAtualSidebar($root, 'moda') || toggleSecao('moda')\"",
            drawer_mobile,
        )
        self.assertIn(
            "@click=\"window.expandirSecaoAtualSidebar($root, 'polpa') || toggleSecao('polpa')\"",
            drawer_mobile,
        )
        self.assertNotIn("@click=\"!collapsed && toggleSecao('moda')\"", drawer_mobile)
        self.assertNotIn("@click=\"!collapsed && toggleSecao('polpa')\"", drawer_mobile)
        self.assertNotIn('<span x-show="!collapsed">{{ grupo.label }}</span>', drawer_mobile)
