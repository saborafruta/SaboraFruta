from decimal import Decimal

from django.template.loader import get_template
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.moda.models import ItemPedidoProducao, PedidoProducao, ProdutoModa
from apps.moda.services.kanban_comercial import COLUNAS
from apps.moda.views_op2 import _sincronizar_status


class Op2Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        empresa = Empresa.objects.create(
            razao_social='OP 2 LTDA', nome_fantasia='OP 2', cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=empresa, razao_social='OP 2 LTDA', cnpj='53345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, tipo_pessoa='J', razao_social='Cliente OP 2', ativo=True,
        )

    def setUp(self):
        self.pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente,
        )
        self.produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='OP2001', nome='Camisa personalizada',
        )

    def _item(self, quantidade=10, status='orcamento', entregue=0):
        return ItemPedidoProducao.objects.create(
            pedido=self.pedido, produto=self.produto, quantidade=quantidade,
            valor_unitario=Decimal('50'), status_fluxo=status,
            quantidade_entregue=entregue,
        )

    def test_entrega_parcial_mantem_op_na_etapa_do_item_pendente(self):
        self._item(status=ItemPedidoProducao.StatusFluxo.ENTREGUE, entregue=10)
        pendente = self._item(status=ItemPedidoProducao.StatusFluxo.PRODUCAO)

        _sincronizar_status(self.pedido)
        self.pedido.refresh_from_db()

        self.assertTrue(self.pedido.entrega_parcial)
        self.assertEqual(self.pedido.status, PedidoProducao.Status.EM_PRODUCAO)
        pendente.status_fluxo = ItemPedidoProducao.StatusFluxo.PRONTO
        pendente.save(update_fields=['status_fluxo'])
        _sincronizar_status(self.pedido)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.status, PedidoProducao.Status.PRONTO)

    def test_todos_os_produtos_entregues_encerram_op(self):
        self._item(status=ItemPedidoProducao.StatusFluxo.ENTREGUE, entregue=10)
        self._item(status=ItemPedidoProducao.StatusFluxo.ENTREGUE, entregue=10)

        _sincronizar_status(self.pedido)
        self.pedido.refresh_from_db()

        self.assertEqual(self.pedido.status, PedidoProducao.Status.ENTREGUE)
        self.assertFalse(self.pedido.entrega_parcial)

    def test_rotas_e_templates_da_versao_nova_sao_separados(self):
        self.assertEqual(reverse('moda:op2-create'), '/moda/comercial/op-2/novo/')
        self.assertIn('/op-2/', reverse('moda:op2-detail', args=[self.pedido.pk]))
        get_template('moda/op2_create.html')
        get_template('moda/op2_detail.html')

    def test_kanban_nao_oferece_coluna_aguardando_material(self):
        self.assertNotIn('material', [coluna.chave for coluna in COLUNAS])
        self.assertIn('Pronto para retirada', [coluna.label for coluna in COLUNAS])
