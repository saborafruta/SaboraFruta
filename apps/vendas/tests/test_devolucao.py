"""Devolucao com apresentacao (ex: devolver 2 caixas em vez de digitar
direto na unidade base) -- mesmo espirito da conexao ja feita em
apps/compras/tests/test_apresentacao_compra.py, agora em
VendaService.criar_devolucao."""
from decimal import Decimal

from django.test import TestCase

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.produtos.models import Produto, ProdutoApresentacao, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial
from apps.vendas.models import ItemDevolucao, PedidoVenda
from apps.vendas.services.venda_service import VendaService


class DevolucaoApresentacaoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Devolucao Apresentacao LTDA',
            cnpj='42345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Devolucao Apresentacao',
            cnpj='42345678000192',
            uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Operador', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='devolucao-apresentacao@inoovated.com',
            nome='Usuario Devolucao',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        cls.cx = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='CX', descricao='Caixa',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        UnidadeMedidaFilial.objects.create(unidade=cls.cx, filial=cls.filial)
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, tipo_pessoa='J', razao_social='Cliente Devolucao',
            cpf_cnpj='42345678000193', uf='RN',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)

    def criar_produto(self, descricao='Produto Devolucao', controla_lote=False):
        produto = Produto.objects.create(
            filial=self.filial,
            unidade_medida=self.unidade,
            descricao=descricao,
            ncm='20089900',
            controla_lote=controla_lote,
            controla_validade=controla_lote,
            permite_venda_sem_estoque=False,
            preco_venda=Decimal('10.00'),
            preco_custo=Decimal('4.00'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        return produto

    def abastecer(self, produto, quantidade='100', valor='4.00'):
        return MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(quantidade),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal(valor),
        )

    def criar_pedido_faturado(self, produto, quantidade):
        """Percorre todo o ciclo de vida do pedido ate FATURADO -- pre-requisito
        de VendaService.criar_devolucao."""
        pedido = VendaService.criar_pedido(self.filial, self.usuario, self.cliente)
        item = VendaService.adicionar_item(
            pedido=pedido,
            produto=produto,
            quantidade=quantidade,
            valor_unitario=Decimal('10.00'),
        )
        VendaService.confirmar_pedido(pedido, self.usuario)
        VendaService.faturar_pedido(pedido, self.usuario)
        pedido.refresh_from_db()
        item.refresh_from_db()
        return pedido, item

    def test_devolucao_de_2_caixas12_retorna_24_un_ao_estoque(self):
        produto = self.criar_produto()
        self.abastecer(produto, quantidade='100')
        caixa12 = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 12', fator_conversao=12,
        )
        pedido, item = self.criar_pedido_faturado(produto, Decimal('100'))
        estoque_antes = Estoque.objects.get(produto=produto, filial=self.filial)

        devolucao = VendaService.criar_devolucao(
            pedido=pedido,
            usuario=self.usuario,
            motivo='defeito',
            itens_devolvidos=[{
                'item_pedido_id': item.pk,
                'quantidade': Decimal('2'),
                'retornar_ao_estoque': True,
                'apresentacao': caixa12,
            }],
        )

        item_devolucao = ItemDevolucao.objects.get(devolucao=devolucao)
        self.assertEqual(item_devolucao.quantidade, Decimal('24.000'))
        self.assertEqual(item_devolucao.quantidade_comercial, Decimal('2.000'))
        self.assertEqual(item_devolucao.apresentacao, caixa12)
        self.assertEqual(item_devolucao.apresentacao_fator_conversao, Decimal('12.000000'))

        estoque_depois = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(
            estoque_depois.quantidade_atual - estoque_antes.quantidade_atual,
            Decimal('24.000'),
        )
        self.assertTrue(
            MovimentacaoEstoque.objects.filter(
                produto=produto,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
                documento_id=pedido.pk,
                quantidade=Decimal('24.000'),
            ).exists()
        )

    def test_devolucao_sem_apresentacao_continua_em_unidade_base(self):
        produto = self.criar_produto()
        self.abastecer(produto, quantidade='100')
        pedido, item = self.criar_pedido_faturado(produto, Decimal('10'))

        devolucao = VendaService.criar_devolucao(
            pedido=pedido,
            usuario=self.usuario,
            motivo='defeito',
            itens_devolvidos=[{
                'item_pedido_id': item.pk,
                'quantidade': Decimal('3'),
                'retornar_ao_estoque': True,
            }],
        )

        item_devolucao = ItemDevolucao.objects.get(devolucao=devolucao)
        self.assertEqual(item_devolucao.quantidade, Decimal('3.000'))
        self.assertIsNone(item_devolucao.apresentacao)
        self.assertIsNone(item_devolucao.apresentacao_fator_conversao)
        self.assertIsNone(item_devolucao.quantidade_comercial)

    def test_apresentacao_de_outro_produto_e_rejeitada(self):
        produto = self.criar_produto()
        outro_produto = self.criar_produto(descricao='Outro Produto')
        self.abastecer(produto, quantidade='100')
        apresentacao_outro = ProdutoApresentacao.objects.create(
            produto=outro_produto, unidade=self.cx, descricao='Caixa 12', fator_conversao=12,
        )
        pedido, item = self.criar_pedido_faturado(produto, Decimal('10'))

        with self.assertRaises(DadosInvalidosError):
            VendaService.criar_devolucao(
                pedido=pedido,
                usuario=self.usuario,
                motivo='defeito',
                itens_devolvidos=[{
                    'item_pedido_id': item.pk,
                    'quantidade': Decimal('1'),
                    'retornar_ao_estoque': True,
                    'apresentacao': apresentacao_outro,
                }],
            )

    def test_apresentacao_que_nao_permite_venda_e_rejeitada(self):
        produto = self.criar_produto()
        self.abastecer(produto, quantidade='100')
        caixa12 = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 12', fator_conversao=12,
            permite_venda=False,
        )
        pedido, item = self.criar_pedido_faturado(produto, Decimal('100'))

        with self.assertRaises(DadosInvalidosError):
            VendaService.criar_devolucao(
                pedido=pedido,
                usuario=self.usuario,
                motivo='defeito',
                itens_devolvidos=[{
                    'item_pedido_id': item.pk,
                    'quantidade': Decimal('2'),
                    'retornar_ao_estoque': True,
                    'apresentacao': caixa12,
                }],
            )

    def test_quantidade_em_apresentacao_maior_que_vendido_e_rejeitada(self):
        produto = self.criar_produto()
        self.abastecer(produto, quantidade='100')
        caixa12 = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 12', fator_conversao=12,
        )
        pedido, item = self.criar_pedido_faturado(produto, Decimal('10'))

        with self.assertRaises(DadosInvalidosError):
            VendaService.criar_devolucao(
                pedido=pedido,
                usuario=self.usuario,
                motivo='defeito',
                itens_devolvidos=[{
                    'item_pedido_id': item.pk,
                    'quantidade': Decimal('5'),  # 5 * 12 = 60 UN, so vendeu 10
                    'retornar_ao_estoque': True,
                    'apresentacao': caixa12,
                }],
            )
