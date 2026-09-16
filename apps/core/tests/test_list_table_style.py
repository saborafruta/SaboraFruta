from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class ListTableStyleAssetTests(SimpleTestCase):
    def setUp(self):
        self.project_dir = Path(settings.BASE_DIR)

    def test_base_loads_list_table_assets_after_inline_theme_styles(self):
        template = (self.project_dir / "templates" / "_base.html").read_text(encoding="utf-8")

        style_position = template.index("css/list-table-style.css")
        inline_style_end = template.rindex("</style>", 0, style_position)
        self.assertGreater(style_position, inline_style_end)
        self.assertIn("js/list-table-sticky.js", template)

    def test_list_header_has_light_navy_pattern_and_dark_contrast(self):
        css = (self.project_dir / "static" / "css" / "list-table-style.css").read_text(encoding="utf-8")

        self.assertIn("--erp-list-header-bg: #1b326e", css)
        self.assertIn("color: #e5e7eb !important", css)
        self.assertIn("color: #aeb8c7 !important", css)
        self.assertIn("text-transform: uppercase !important", css)
        self.assertIn(".erp-list-page .table-header a", css)
        self.assertIn(".erp-list-page .table-header span", css)
        self.assertIn(".erp-table-sticky-clone a", css)

    def test_sticky_header_is_limited_to_listing_tables(self):
        script = (self.project_dir / "static" / "js" / "list-table-sticky.js").read_text(encoding="utf-8")

        self.assertIn(".erp-list-page .table-container table", script)
        self.assertIn("[data-no-sticky-table]", script)
        self.assertIn("requestAnimationFrame", script)
        self.assertIn("aria-hidden", script)
        self.assertIn("erp:table-columns-applied", script)
        self.assertIn("cloneColumns.replaceChildren", script)
