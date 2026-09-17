from django.test import TestCase

from apps.fiscal.tasks import _reconciliar_nfce_banco_atual


class ReconciliacaoNFCeTests(TestCase):
    def test_sem_documentos_retorna_total_numerico_para_multitenancy(self):
        self.assertEqual(_reconciliar_nfce_banco_atual(), 0)
