from pathlib import Path

from django.test import SimpleTestCase


class OcorrenciasComerciaisTemplateTests(SimpleTestCase):
    def test_cada_listagem_tem_rolagem_independente(self):
        template = Path(
            'apps/analytics/templates/analytics/ocorrencias_comerciais.html'
        ).read_text(encoding='utf-8')

        self.assertIn('.oc-scroll{max-height:380px;overflow:auto}', template)
        self.assertEqual(template.count('<div class="oc-scroll">'), 3)
        self.assertIn('.oc-table th{text-align:left;color:#64748b;position:sticky', template)
