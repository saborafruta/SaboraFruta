from pathlib import Path

from django.test import SimpleTestCase


class ContaPagarTemplateRegressionTests(SimpleTestCase):
    def test_dias_semana_nao_aplica_widget_tweaks_em_boundwidget_tag(self):
        template = Path(
            "apps/financeiro/templates/financeiro/pagar/form.html"
        ).read_text(encoding="utf-8")

        self.assertNotIn("checkbox.tag|attr", template)
        self.assertIn("{{ checkbox.tag }}", template)
        self.assertIn("configurarRecorrenciaSemanalIntuitiva", template)
