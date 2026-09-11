from django.conf import settings
from django.test import RequestFactory, SimpleTestCase


class UploadLimitsTests(SimpleTestCase):
    def test_formulario_grande_da_op_pode_ser_processado(self):
        quantidade_campos = 2500
        dados = {
            f'item_0_campo_tecnico_{indice}': str(indice)
            for indice in range(quantidade_campos)
        }

        request = RequestFactory().post('/moda/comercial/op-2/novo/', dados)

        self.assertGreaterEqual(
            settings.DATA_UPLOAD_MAX_NUMBER_FIELDS, quantidade_campos,
        )
        self.assertEqual(len(request.POST), quantidade_campos)
