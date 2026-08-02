from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial, Fornecedor, FornecedorFilial
from apps.compras.models import EntradaNF, PedidoCompra
from apps.compras.services.compra_service import CompraService
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque, LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial
from apps.vendas.models import PedidoVenda
from apps.vendas.services.venda_service import VendaService


class EstoqueIntegracoesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Integracao LTDA',
            nome_fantasia='Empresa Integracao',
            cnpj='32345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Integracao',
            nome_fantasia='Matriz',
            cnpj='32345678000192',
            uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Operador',
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='integracao-estoque@inoovated.com',
            nome='Usuario Integracao',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa,
            sigla='UN',
            descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.cliente = Cliente.objects.create(
            filial=cls.filial,
            tipo_pessoa='J',
            razao_social='Cliente Integracao',
            cpf_cnpj='32345678000193',
            uf='RN',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.fornecedor = Fornecedor.objects.create(
            filial=cls.filial,
            tipo_pessoa='J',
            razao_social='Fornecedor Integracao',
            cpf_cnpj='32345678000194',
            uf='RN',
        )
        FornecedorFilial.objects.create(fornecedor=cls.fornecedor, filial=cls.filial)

    def criar_produto(self, descricao='Produto Integracao', controla_lote=False):
        produto = Produto.objects.create(
            filial=self.filial,
            unidade_medida=self.unidade,
            fornecedor=self.fornecedor,
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

    def abastecer(self, produto, quantidade='10', valor='4.00'):
        return MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(quantidade),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal(valor),
        )

    def test_entrada_de_compra_efetivada_cria_lote_saldo_e_movimento(self):
        produto = self.criar_produto(controla_lote=True)
        pedido = CompraService.criar_pedido(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=self.fornecedor,
        )
        item_pedido = CompraService.adicionar_item(
            pedido=pedido,
            produto=produto,
            quantidade=Decimal('5'),
            valor_unitario=Decimal('4.00'),
        )
        entrada = CompraService.criar_entrada_nf(
            filial=self.filial,
            usuario=self.usuario,
            fornecedor=self.fornecedor,
            numero_nf='NF-INT-001',
            serie_nf='1',
            data_emissao_nf=timezone.localdate(),
            pedido_compra=pedido,
        )
        CompraService.adicionar_item_entrada(
            entrada=entrada,
            produto=produto,
            quantidade=Decimal('5'),
            valor_unitario=Decimal('4.00'),
            numero_lote='COMPRA-INT-001',
            data_validade=timezone.localdate() + timedelta(days=90),
            item_pedido_compra=item_pedido,
        )

        CompraService.efetivar_entrada(entrada, self.usuario)

        entrada.refresh_from_db()
        pedido.refresh_from_db()
        item_pedido.refresh_from_db()
        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        lote = LoteProduto.objects.get(produto=produto, filial=self.filial)
        movimento = MovimentacaoEstoque.objects.get(produto=produto, lote=lote)
        self.assertEqual(entrada.status, EntradaNF.Status.EFETIVADA)
        self.assertEqual(pedido.status, PedidoCompra.Status.RECEBIDO)
        self.assertEqual(item_pedido.quantidade_recebida, Decimal('5.000'))
        self.assertEqual(estoque.quantidade_atual, Decimal('5.000'))
        self.assertEqual(estoque.quantidade_disponivel, Decimal('5.000'))
        self.assertEqual(lote.quantidade_atual, Decimal('5.000'))
        self.assertEqual(movimento.documento_tipo, MovimentacaoEstoque.DocumentoTipo.NFE)

    def test_pedido_confirmado_reserva_e_cancelamento_libera_reserva(self):
        produto = self.criar_produto()
        self.abastecer(produto, quantidade='10')
        pedido = VendaService.criar_pedido(self.filial, self.usuario, self.cliente)
        VendaService.adicionar_item(
            pedido=pedido,
            produto=produto,
            quantidade=Decimal('4'),
            valor_unitario=Decimal('10.00'),
        )

        VendaService.confirmar_pedido(pedido, self.usuario)
        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_reservada, Decimal('4.000'))
        self.assertEqual(estoque.quantidade_disponivel, Decimal('6.000'))

        VendaService.cancelar_pedido(pedido, self.usuario, 'Teste de cancelamento')

        pedido.refresh_from_db()
        estoque.refresh_from_db()
        self.assertEqual(pedido.status, PedidoVenda.Status.CANCELADO)
        self.assertEqual(estoque.quantidade_reservada, Decimal('0.000'))
        self.assertEqual(estoque.quantidade_disponivel, Decimal('10.000'))

    def test_pedido_faturado_baixa_estoque_e_libera_reserva(self):
        produto = self.criar_produto()
        self.abastecer(produto, quantidade='10')
        pedido = VendaService.criar_pedido(self.filial, self.usuario, self.cliente)
        item = VendaService.adicionar_item(
            pedido=pedido,
            produto=produto,
            quantidade=Decimal('4'),
            valor_unitario=Decimal('10.00'),
        )
        VendaService.confirmar_pedido(pedido, self.usuario)

        VendaService.faturar_pedido(pedido, self.usuario)

        pedido.refresh_from_db()
        item.refresh_from_db()
        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(pedido.status, PedidoVenda.Status.FATURADO)
        self.assertEqual(item.quantidade_atendida, Decimal('4.000'))
        self.assertEqual(estoque.quantidade_atual, Decimal('6.000'))
        self.assertEqual(estoque.quantidade_reservada, Decimal('0.000'))
        self.assertEqual(estoque.quantidade_disponivel, Decimal('6.000'))
        self.assertTrue(
            MovimentacaoEstoque.objects.filter(
                produto=produto,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.PEDIDO_VENDA,
                documento_id=pedido.pk,
            ).exists()
        )

    def test_venda_usa_preco_promocional_sem_alterar_regra_de_estoque(self):
        produto = self.criar_produto()
        produto.preco_promocional = Decimal('7.00')
        produto.promocao_tipo_desconto = 'preco_final'
        produto.promocao_valor_desconto = Decimal('7.00')
        produto.promocao_dias_semana = '0,1,2,3,4,5,6'
        produto.save(update_fields=[
            'preco_promocional',
            'promocao_tipo_desconto',
            'promocao_valor_desconto',
            'promocao_dias_semana',
            'updated_at',
        ])
        ProdutoFilial.objects.filter(produto=produto, filial=self.filial).update(
            preco_promocional=Decimal('7.00'),
            preco_promocional_ativo=True,
            promocao_tipo_desconto='preco_final',
            promocao_valor_desconto=Decimal('7.00'),
            promocao_dias_semana='0,1,2,3,4,5,6',
        )
        self.abastecer(produto, quantidade='10', valor='4.00')
        pedido = VendaService.criar_pedido(self.filial, self.usuario, self.cliente)

        item = VendaService.adicionar_item(
            pedido=pedido,
            produto=produto,
            quantidade=Decimal('2'),
        )
        VendaService.confirmar_pedido(pedido, self.usuario)
        VendaService.faturar_pedido(pedido, self.usuario)

        item.refresh_from_db()
        produto.refresh_from_db()
        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(item.valor_unitario, Decimal('7.00'))
        self.assertEqual(estoque.quantidade_atual, Decimal('8.000'))
        self.assertEqual(estoque.quantidade_reservada, Decimal('0.000'))
        self.assertEqual(produto.preco_custo_medio, Decimal('4.0000'))

    def test_pedido_em_separacao_pode_cancelar_e_liberar_reserva(self):
        produto = self.criar_produto()
        self.abastecer(produto, quantidade='10')
        pedido = VendaService.criar_pedido(self.filial, self.usuario, self.cliente)
        VendaService.adicionar_item(
            pedido=pedido,
            produto=produto,
            quantidade=Decimal('3'),
            valor_unitario=Decimal('10.00'),
        )
        VendaService.confirmar_pedido(pedido, self.usuario)
        VendaService.separar_pedido(pedido, self.usuario)

        VendaService.cancelar_pedido(pedido, self.usuario, 'Cancelado apos separacao')

        pedido.refresh_from_db()
        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(pedido.status, PedidoVenda.Status.CANCELADO)
        self.assertEqual(estoque.quantidade_atual, Decimal('10.000'))
        self.assertEqual(estoque.quantidade_reservada, Decimal('0.000'))
        self.assertEqual(estoque.quantidade_disponivel, Decimal('10.000'))
