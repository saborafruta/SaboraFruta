from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from types import SimpleNamespace


class VendasListTemplateTests(SimpleTestCase):
    def test_renderiza_com_o_template_base_global(self):
        page = Paginator([], 20).get_page(1)
        request = SimpleNamespace(
            GET={},
            path="/pdv/vendas/",
            resolver_match=SimpleNamespace(url_name="vendas_list"),
        )
        user = SimpleNamespace(is_authenticated=True, is_superuser=False)

        html = render_to_string(
            "pdv/vendas_list.html",
            {"page": page, "request": request, "user": user},
        )

        self.assertIn("Vendas PDV", html)
        self.assertIn("Nenhuma venda registrada", html)
