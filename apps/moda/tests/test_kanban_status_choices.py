from django.test import SimpleTestCase

from apps.moda.models import PedidoProducao
from apps.moda.services.kanban_comercial import (
    COLUNAS,
    status_choices_kanban,
    status_destino_kanban,
)


class KanbanStatusChoicesTests(SimpleTestCase):
    def test_choices_match_kanban_destinations(self):
        self.assertEqual(
            status_choices_kanban(),
            [(coluna.destino, coluna.label) for coluna in COLUNAS],
        )

    def test_preserves_internal_production_status(self):
        self.assertEqual(
            status_destino_kanban(PedidoProducao.Status.EM_ACABAMENTO),
            PedidoProducao.Status.LIBERADO_PRODUCAO,
        )

    def test_does_not_offer_status_outside_kanban(self):
        values = {value for value, _ in status_choices_kanban()}
        self.assertNotIn(PedidoProducao.Status.CANCELADO, values)
