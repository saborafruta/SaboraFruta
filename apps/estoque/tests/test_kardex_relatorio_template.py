from pathlib import Path

from django.test import SimpleTestCase
from django.urls import reverse


class KardexRelatorioTemplateTests(SimpleTestCase):
    def setUp(self):
        self.template = Path(
            'apps/estoque/templates/estoque/relatorios/kardex.html'
        ).read_text(encoding='utf-8')

    def test_autocomplete_seleciona_produto_e_preserva_filtros_do_formulario(self):
        self.assertIn('name="produto"', self.template)
        self.assertIn('role="combobox"', self.template)
        self.assertIn('aria-controls="kardex-results"', self.template)
        self.assertIn("productId.value = product.id", self.template)
        self.assertIn('form.submit()', self.template)
        self.assertEqual(
            reverse('estoque:relatorio-kardex-produtos'),
            '/estoque/relatorios/kardex/produtos/',
        )
