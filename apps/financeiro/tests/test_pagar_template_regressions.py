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

    def test_edicao_em_lote_usa_busca_de_categoria_no_modal(self):
        template = Path(
            "apps/financeiro/templates/financeiro/pagar/list.html"
        ).read_text(encoding="utf-8")

        self.assertIn('name="plano_contas" :value="loteCategoriaId"', template)
        self.assertIn("loteCategoriaQuery", template)
        self.assertNotIn("bulk_edit_form.plano_contas|add_class", template)
