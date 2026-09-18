from pathlib import Path

from django.test import SimpleTestCase


class SidebarSearchTemplateTests(SimpleTestCase):
    def test_sidebar_carrega_busca_no_desktop_e_mobile(self):
        raiz = Path(__file__).resolve().parents[1]
        sidebar = (raiz / "templates" / "core" / "_sidebar.html").read_text(encoding="utf-8")
        navegacao = (raiz / "templates" / "core" / "_sidebar_navigation.html").read_text(encoding="utf-8")
        busca = (raiz / "templates" / "core" / "_sidebar_search.html").read_text(encoding="utf-8")

        self.assertIn('core/js/sidebar_search.js', sidebar)
        self.assertIn('core/css/sidebar_search.css', sidebar)
        self.assertIn('?v=20260917-5', sidebar)
        self.assertIn('placeholder="Buscar"', busca)
        self.assertIn("border-right:1px solid #cbd5e1", sidebar)
        self.assertIn("color:#1f2937", sidebar)
        self.assertIn("color:#1f2937", navegacao)
        self.assertNotIn("color:#6b7280", sidebar)
        self.assertNotIn("color:#6b7280", navegacao)
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
        self.assertIn('display: none', styles)
        self.assertIn('.sidebar-menu-search.is-stuck::before', styles)
        self.assertIn('display: block', styles)
        self.assertIn('right: -16px', styles)
        self.assertIn('left: -16px', styles)
        self.assertIn('height: 24px', styles)
        self.assertIn('--sidebar-search-shelf: #18181b', styles)
        self.assertIn('color: #475569', styles)
        self.assertIn('color: #64748b', styles)
        self.assertNotIn('repeating-linear-gradient', styles)
        self.assertIn('border-color: #64748b', styles)
        self.assertIn('color: #334155', styles)

    def test_menu_especial_do_pdv_carrega_busca_completa(self):
        raiz_apps = Path(__file__).resolve().parents[2]
        template = (raiz_apps / "pdv" / "templates" / "pdv" / "home.html").read_text(encoding="utf-8")
        sidebar = (raiz_apps / "core" / "templates" / "core" / "_sidebar.html").read_text(encoding="utf-8")

        self.assertIn('{% include "core/_sidebar.html" with sidebar_assets_only=True %}', template)
        self.assertIn('core/css/sidebar_search.css', sidebar)
        self.assertIn('?v=20260917-5', sidebar)
        self.assertIn('core/js/sidebar_search.js', template)
        self.assertIn('sidebar_favorites.js', template)
        self.assertIn('v=20260917-3', template)
