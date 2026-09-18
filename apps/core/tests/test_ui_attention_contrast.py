from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class UiAttentionContrastTests(SimpleTestCase):
    """Protege descoberta de Colunas e contraste de tags nos dois temas."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = Path(settings.BASE_DIR)

    def _read(self, relative_path):
        return (self.root / relative_path).read_text(encoding='utf-8')

    def test_columns_action_is_highlighted_for_generic_and_product_tables(self):
        css = self._read('static/css/ui-attention-contrast.css')

        self.assertIn('.erp-table-columns-toolbar .btn-secondary', css)
        self.assertIn('[data-product-column-picker-trigger]', css)
        self.assertIn('border: 2px solid #f59e0b !important', css)
        self.assertIn('[aria-expanded="true"]', css)

    def test_product_picker_opens_inside_the_viewport(self):
        css = self._read('static/css/ui-attention-contrast.css')

        self.assertIn('[data-product-column-picker] .produto-column-picker-panel', css)
        self.assertIn('right: 0 !important', css)
        self.assertIn('left: auto !important', css)
        self.assertIn('max-height: calc(100vh - 24px) !important', css)

        javascript = self._read('static/js/tabelas-configuraveis.js')
        self.assertIn('const top = Math.max(', javascript)
        self.assertIn('instance.panel.style.top = `${top}px`', javascript)

    def test_dashboard_comparison_uses_aligned_compact_controls(self):
        css = self._read('static/css/ui-attention-contrast.css')
        dashboard = self._read('apps/core/templates/core/dashboard.html')

        self.assertIn('dashboard-sales-breakdown', dashboard)
        self.assertEqual(dashboard.count('class="dashboard-sales-pane"'), 2)
        self.assertEqual(dashboard.count('dashboard-sales-pane-head'), 2)
        self.assertIn('.dashboard-sales-breakdown .dashboard-sales-pane-head', css)
        self.assertIn('height: 56px', css)
        self.assertIn('min-height: 56px', css)
        self.assertIn('min-height: 30px !important', css)
        self.assertIn('height: 30px !important', css)
        self.assertIn('border-width: 1px !important', css)

    def test_dark_theme_repairs_legacy_pills_and_muted_text(self):
        css = self._read('static/css/ui-attention-contrast.css')

        self.assertIn('.rounded-full).bg-gray-100', css)
        self.assertIn('background: #334155 !important', css)
        self.assertIn('color: #f8fafc !important', css)
        self.assertIn('[style^="color:#555"]', css)
        self.assertIn('color: #cbd5e1 !important', css)

    def test_client_type_uses_semantic_global_tags(self):
        template = self._read('apps/cadastros/templates/cadastros/cliente/list.html')

        self.assertIn('erp-tag-neutral', template)
        self.assertIn('erp-tag-purple', template)
        self.assertIn('erp-tag-blue', template)
        self.assertNotIn('style="color:#555;"', template)

    def test_client_columns_control_uses_free_space_in_the_filters(self):
        template = self._read('apps/cadastros/templates/cadastros/cliente/list.html')
        javascript = self._read('static/js/tabelas-configuraveis.js')

        self.assertIn('data-erp-table-columns-slot', template)
        self.assertIn('const columnsSlot =', javascript)
        self.assertIn('erp-table-columns-toolbar--in-filter', javascript)

    def test_global_layer_is_loaded_after_existing_contrast_styles(self):
        template = self._read('templates/_base.html')
        light_index = template.index('light-theme-contrast.css')
        global_index = template.index('ui-attention-contrast.css')

        self.assertLess(light_index, global_index)
        self.assertIn('ui-attention-contrast.css', template)
        self.assertIn("tabelas-configuraveis.js' %}?v=20260918-1", template)
