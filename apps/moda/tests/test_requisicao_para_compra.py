"""
A requisição do PCP virando pedido de compra — o caminho que nunca rodou.

`IntegracaoService.gerar_pedido_compra` existia, tinha view, tinha botão na
tela, e NUNCA foi executado: nem por teste, nem por clique que alguém tenha
relatado. Tinha dois defeitos que só aparecem na primeira execução:

  1. `PedidoCompra(observacoes=...)` — o campo do modelo é `observacao`,
     singular. Django recusa kwarg desconhecido, então a criação estourava
     `TypeError` antes de gravar qualquer coisa;

  2. `PedidoCompra.all_objects` — o modelo herda o manager de
     `FilialScopedModel` e não declara o irrestrito. `AttributeError` na
     numeração do pedido.

Os dois foram encontrados ao escrever o equivalente para o vertical de polpa,
que precisava do mesmo caminho. Este arquivo existe para que o de moda não
volte a apodrecer em silêncio.
"""
from decimal import Decimal

from django.test import TestCase

from apps.cadastros.models import Fornecedor
from apps.compras.models import PedidoCompra
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.moda.models import ItemRequisicao, RequisicaoMaterial
from apps.moda.services.integracao import IntegracaoService
from apps.produtos.models import Produto, UnidadeMedida

ZERO = Decimal('0')


class RequisicaoParaCompraTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Compra LTDA', nome_fantasia='Compra',
            cnpj='53345678000191', segmento='moda_confeccao',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='53345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='M', descricao='Metro',
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@compra.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.fornecedor = Fornecedor.objects.create(
            filial=cls.filial, razao_social='Tecidos do Norte',
            cpf_cnpj='11122233344',
        )

    def setUp(self):
        self.tecido = Produto.objects.create(
            filial=self.filial, codigo='TEC001', descricao='Malha PV',
            unidade_medida=self.unidade, preco_custo=Decimal('18'),
        )

    def _requisicao(self, com_produto=True):
        requisicao = RequisicaoMaterial.objects.create(
            filial=self.filial, criado_por=self.usuario,
        )
        ItemRequisicao.objects.create(
            requisicao=requisicao,
            produto=self.tecido if com_produto else None,
            descricao='Malha PV', codigo='TEC001', unidade='M',
            quantidade=Decimal('120'),
        )
        return requisicao

    # ── O caminho feliz, que estourava ───────────────────────────────────

    def test_a_requisicao_vira_pedido_de_compra(self):
        requisicao = self._requisicao()

        pedido, gerados, ignorados = IntegracaoService.gerar_pedido_compra(
            requisicao, self.fornecedor, self.usuario,
        )

        self.assertEqual(gerados, 1)
        self.assertEqual(ignorados, 0)
        self.assertEqual(pedido.fornecedor, self.fornecedor)
        self.assertEqual(pedido.itens.get().quantidade, Decimal('120.000'))

    def test_o_pedido_diz_de_onde_veio(self):
        """
        O campo é `observacao`, singular — com o plural o Django recusava o
        kwarg e nada era gravado.
        """
        requisicao = self._requisicao()

        pedido, _g, _i = IntegracaoService.gerar_pedido_compra(
            requisicao, self.fornecedor, self.usuario,
        )

        self.assertIn(f'#{requisicao.numero:04d}', pedido.observacao)

    def test_a_numeracao_do_pedido_funciona(self):
        """
        `all_objects` não existe em `PedidoCompra` — era AttributeError antes
        de o pedido nascer.
        """
        primeiro = self._requisicao()
        pedido_a, _g, _i = IntegracaoService.gerar_pedido_compra(
            primeiro, self.fornecedor, self.usuario,
        )

        segundo = self._requisicao()
        pedido_b, _g, _i = IntegracaoService.gerar_pedido_compra(
            segundo, self.fornecedor, self.usuario,
        )

        self.assertNotEqual(pedido_a.numero_pedido, pedido_b.numero_pedido)
        self.assertEqual(PedidoCompra.objects.count(), 2)

    def test_o_preco_parte_do_custo_e_nao_de_zero(self):
        """Um pedido com valor zerado passa despercebido na aprovação."""
        requisicao = self._requisicao()

        pedido, _g, _i = IntegracaoService.gerar_pedido_compra(
            requisicao, self.fornecedor, self.usuario,
        )

        self.assertEqual(pedido.itens.get().valor_unitario, Decimal('18.0000'))
        self.assertEqual(pedido.valor_total, Decimal('2160.00'))

    # ── As travas que já estavam certas ──────────────────────────────────

    def test_a_requisicao_nao_vira_atendida_na_emissao(self):
        """Atendida é quando o material CHEGA, não quando se manda comprar."""
        requisicao = self._requisicao()

        IntegracaoService.gerar_pedido_compra(
            requisicao, self.fornecedor, self.usuario,
        )

        requisicao.refresh_from_db()
        self.assertEqual(requisicao.status, RequisicaoMaterial.Status.ABERTA)
        self.assertIsNotNone(requisicao.pedido_compra_id)

    def test_nao_gera_o_mesmo_pedido_duas_vezes(self):
        from apps.core.services.exceptions import DomainError

        requisicao = self._requisicao()
        IntegracaoService.gerar_pedido_compra(
            requisicao, self.fornecedor, self.usuario,
        )

        with self.assertRaises(DomainError):
            IntegracaoService.gerar_pedido_compra(
                requisicao, self.fornecedor, self.usuario,
            )

        self.assertEqual(PedidoCompra.objects.count(), 1)

    def test_linha_sem_produto_de_estoque_nao_vira_item(self):
        """
        Comprar exige produto cadastrado. A linha solta fica na requisição,
        visível, em vez de sumir num pedido incompleto.
        """
        from apps.core.services.exceptions import DomainError

        requisicao = self._requisicao(com_produto=False)

        with self.assertRaises(DomainError):
            IntegracaoService.gerar_pedido_compra(
                requisicao, self.fornecedor, self.usuario,
            )
