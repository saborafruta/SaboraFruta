from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class ListHeaderStandardTests(SimpleTestCase):
    """Impede que listagens voltem a criar cabeçalhos visuais isolados."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = Path(settings.BASE_DIR)

    def _read(self, relative_path):
        return (self.root / relative_path).read_text(encoding='utf-8')

    def test_global_styles_apply_to_every_table_in_application_content(self):
        css = self._read('static/css/list-table-style.css')

        self.assertIn('.app-content-frame .table-header th', css)
        self.assertIn('background: var(--erp-list-header-bg) !important', css)
        self.assertIn('text-transform: uppercase !important', css)
        self.assertNotIn('.erp-list-page .table-header th,', css)

    def test_javascript_normalizes_headers_without_per_page_opt_in(self):
        javascript = self._read('static/js/list-table-sticky.js')

        self.assertIn('function normalizeTableHeaders(root)', javascript)
        self.assertIn(
            '.app-content-frame table:not([data-erp-table-style="custom"])',
            javascript,
        )
        self.assertIn("header.classList.add('table-header')", javascript)
        self.assertIn('window.erpNormalizeTableHeaders', javascript)

    def test_sales_history_uses_shared_list_and_sticky_table_structure(self):
        template = self._read('apps/analytics/templates/analytics/vendas.html')

        self.assertIn('class="erp-list-page"', template)
        self.assertIn('class="hv-table-wrap table-container"', template)
        self.assertIn('data-sticky-list-container', template)
        self.assertIn('data-sticky-list-table', template)

    def test_base_uses_current_standard_assets(self):
        template = self._read('templates/_base.html')

        self.assertIn('list-table-style.css', template)
        self.assertIn('list-table-sticky.js', template)
        self.assertIn('?v=20260917-1', template)
