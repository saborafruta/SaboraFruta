from pathlib import Path

from django.test import SimpleTestCase


class SidebarSearchTemplateTests(SimpleTestCase):
    def test_sidebar_carrega_busca_no_desktop_e_mobile(self):
        raiz = Path(__file__).resolve().parents[1]
        sidebar = (raiz / "templates" / "core" / "_sidebar.html").read_text(encoding="utf-8")
        navegacao = (raiz / "templates" / "core" / "_sidebar_navigation.html").read_text(encoding="utf-8")

        self.assertIn('core/js/sidebar_search.js', sidebar)
        self.assertIn('core/css/sidebar_search.css', sidebar)
        self.assertIn('?v=20260916-2', sidebar)
        self.assertIn('{% include "core/_sidebar_search.html" %}', sidebar)
        self.assertIn('{% include "core/_sidebar_search.html" %}', navegacao)

    def test_script_tem_busca_sem_acentos_e_navegacao_por_teclado(self):
        raiz = Path(__file__).resolve().parents[1]
        script = (raiz / "static" / "core" / "js" / "sidebar_search.js").read_text(encoding="utf-8")

        self.assertIn("normalize('NFD')", script)
        self.assertIn("ArrowDown", script)
        self.assertIn("ArrowUp", script)
        self.assertIn("event.key === 'Enter'", script)
        self.assertIn("logo.insertAdjacentElement('afterend', box)", script)
        self.assertIn("nav.addEventListener('scroll'", script)
        self.assertIn("classList.toggle('is-stuck'", script)

    def test_busca_sticky_tem_prateleira_e_foco_integrados(self):
        raiz = Path(__file__).resolve().parents[1]
        styles = (raiz / "static" / "core" / "css" / "sidebar_search.css").read_text(encoding="utf-8")

        self.assertIn('.sidebar-menu-search.is-stuck', styles)
        self.assertIn('backdrop-filter:', styles)
        self.assertIn('.sidebar-menu-search-field:focus-within', styles)
        self.assertIn('.sidebar-menu-search::before', styles)
        self.assertIn('--sidebar-search-shelf: #18181b', styles)

    def test_menu_especial_do_pdv_carrega_busca_completa(self):
        raiz_apps = Path(__file__).resolve().parents[2]
        template = (raiz_apps / "pdv" / "templates" / "pdv" / "home.html").read_text(encoding="utf-8")

        self.assertIn('core/css/sidebar_search.css', template)
        self.assertIn('?v=20260916-2', template)
        self.assertIn('core/js/sidebar_search.js', template)
        self.assertIn('sidebar_favorites.js', template)
        self.assertIn('v=20260916-2', template)
