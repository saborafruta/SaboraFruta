from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class LightThemeContrastAssetTests(SimpleTestCase):
    def setUp(self):
        self.project_dir = Path(settings.BASE_DIR)
        self.template = (self.project_dir / "templates" / "_base.html").read_text(
            encoding="utf-8"
        )
        self.css = (
            self.project_dir / "static" / "css" / "light-theme-contrast.css"
        ).read_text(encoding="utf-8")

    def test_contrast_layer_is_loaded_after_legacy_list_styles(self):
        self.assertGreater(
            self.template.index("css/light-theme-contrast.css"),
            self.template.index("css/list-table-style.css"),
        )

    def test_light_theme_has_documented_text_hierarchy(self):
        self.assertIn("--erp-text-strong: #111827", self.css)
        self.assertIn("--erp-text-primary: #1f2937", self.css)
        self.assertIn("--erp-text-secondary: #475569", self.css)
        self.assertIn("--erp-text-muted: #64748b", self.css)
        self.assertIn("Hierarquia adotada", self.css)

    def test_legacy_gray_utilities_and_inline_colors_are_covered(self):
        for utility in (
            ".text-gray-400",
            ".text-gray-500",
            ".text-gray-600",
            ".text-slate-400",
            ".text-slate-500",
            ".text-slate-600",
        ):
            self.assertIn(utility, self.css)

        for legacy_color in ("#64748b", "#6b7280", "#94a3b8", "#9ca3af"):
            self.assertIn(f'[style^="color:{legacy_color}"]', self.css)

    def test_rules_are_scoped_to_light_theme(self):
        self.assertNotIn("body:not(.tema-claro)", self.css)
        self.assertNotIn("body.tema-escuro", self.css)
        self.assertGreaterEqual(self.css.count("body.tema-claro"), 8)
