"""Apresentacao conectada na entrada de compra (CompraService.adicionar_item_entrada
+ AdicionarItemEntradaForm/View), no mesmo espirito da conexao ja feita em
apps/pdv (venda) -- ver apps/compras/services/compra_service.py."""
from decimal import Decimal

from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Fornecedor, FornecedorFilial
from apps.compras.models import EntradaNF, ItemEntradaNF
from apps.compras.services.compra_service import CompraService
from apps.compras.views import AdicionarItemEntradaView
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.produtos.models import Produto, ProdutoApresentacao, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class ApresentacaoCompraBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Compra Apresentacao LTDA', cnpj='51234567000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL, codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Compra Apresentacao', cnpj='51234567000192', uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='compra-apresentacao@inoovated.com', nome='Comprador', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.un = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='UN', descricao='Unidade')
        cls.pct = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='PCT', descricao='Pacote')
        UnidadeMedidaFilial.objects.create(unidade=cls.un, filial=cls.filial)
        UnidadeMedidaFilial.objects.create(unidade=cls.pct, filial=cls.filial)
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.un, descricao='Embalagem Pote 500ml',
            codigo='1001', ncm='39235000', controla_lote=False,
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)
        cls.pacote500 = ProdutoApresentacao.objects.create(
            produto=cls.produto, unidade=cls.pct, descricao='Pacote 500', fator_conversao=500,
        )
        cls.fornecedor = Fornecedor.objects.create(
            filial=cls.filial, tipo_pessoa='J', razao_social='Fornecedor Teste', cpf_cnpj='11222333000144', uf='RN',
        )
        FornecedorFilial.objects.create(fornecedor=cls.fornecedor, filial=cls.filial)

    def criar_entrada(self, **overrides):
        dados = {
            'filial': self.filial, 'fornecedor': self.fornecedor, 'numero_nf': 'NF-APRES-1', 'serie_nf': '1',
            'origem_entrada': EntradaNF.OrigemEntrada.MANUAL, 'data_emissao_nf': timezone.localdate(),
            'data_entrada': timezone.now(), 'status': EntradaNF.Status.RASCUNHO, 'usuario': self.usuario,
        }
        dados.update(overrides)
        return EntradaNF.objects.create(**dados)


class CompraServiceApresentacaoTests(ApresentacaoCompraBase):
    """Caso 7 da secao 11 do documento: compra de 10 PCT500 -> entrada de 5.000 UN."""

    def test_compra_de_10_pct500_gera_entrada_de_5000_un(self):
        entrada = self.criar_entrada()
        item = CompraService.adicionar_item_entrada(
            entrada=entrada, produto=self.produto,
            quantidade=Decimal('10'),  # 10 pacotes, nao 10 UN
            valor_unitario=Decimal('450.00'),  # preco por PACOTE
            apresentacao=self.pacote500,
        )
        self.assertEqual(item.quantidade_estoque, Decimal('5000.000'))
        self.assertEqual(item.quantidade, Decimal('5000.000'))
        self.assertEqual(item.fator_conversao, Decimal('500.0000'))
        self.assertEqual(item.unidade_xml, 'PCT')
        # Custo por unidade base: 450 (preco do pacote) / 500 (fator) = 0.90/UN.
        self.assertEqual(item.valor_unitario, Decimal('0.9000'))
        self.assertEqual(item.apresentacao, self.pacote500)

    def test_apresentacao_de_outro_produto_e_rejeitada(self):
        outro_produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.un, descricao='Outro produto',
            codigo='1002', ncm='39235000',
        )
        ProdutoFilial.objects.create(produto=outro_produto, filial=self.filial)
        entrada = self.criar_entrada()
        with self.assertRaises(DadosInvalidosError):
            CompraService.adicionar_item_entrada(
                entrada=entrada, produto=outro_produto, quantidade=Decimal('1'),
                valor_unitario=Decimal('10'), apresentacao=self.pacote500,
            )

    def test_apresentacao_que_nao_permite_compra_e_rejeitada(self):
        self.pacote500.permite_compra = False
        self.pacote500.save(update_fields=['permite_compra'])
        entrada = self.criar_entrada()
        with self.assertRaises(DadosInvalidosError):
            CompraService.adicionar_item_entrada(
                entrada=entrada, produto=self.produto, quantidade=Decimal('1'),
                valor_unitario=Decimal('10'), apresentacao=self.pacote500,
            )

    def test_fator_manual_e_ignorado_quando_apresentacao_e_informada(self):
        entrada = self.criar_entrada()
        item = CompraService.adicionar_item_entrada(
            entrada=entrada, produto=self.produto, quantidade=Decimal('2'),
            valor_unitario=Decimal('450.00'), apresentacao=self.pacote500,
            fator_conversao=Decimal('999'),  # deve ser ignorado
        )
        self.assertEqual(item.fator_conversao, Decimal('500.0000'))
        self.assertEqual(item.quantidade_estoque, Decimal('1000.000'))


class AdicionarItemEntradaViewApresentacaoTests(ApresentacaoCompraBase):
    """Integracao form/view: apresentacao escolhida no POST chega no item criado."""

    def setUp(self):
        self.factory = RequestFactory()

    def post(self, entrada, data):
        request = self.factory.post(
            reverse('compras:entrada-add-item', args=[entrada.pk]), data,
        )
        request.user = self.usuario
        request.filial_ativa = self.filial
        request.session = {}
        request._messages = FallbackStorage(request)
        return AdicionarItemEntradaView.as_view()(request, pk=entrada.pk)

    def test_post_com_apresentacao_cria_item_convertido(self):
        entrada = self.criar_entrada()
        response = self.post(entrada, {
            'produto': self.produto.pk,
            'apresentacao': self.pacote500.pk,
            'quantidade': '10',
            'valor_unitario': '450.00',
        })
        self.assertEqual(response.status_code, 302)
        item = ItemEntradaNF.objects.get(entrada=entrada)
        self.assertEqual(item.apresentacao, self.pacote500)
        self.assertEqual(item.quantidade_estoque, Decimal('5000.000'))
        self.assertEqual(item.unidade_xml, 'PCT')

    def test_post_sem_apresentacao_continua_funcionando_com_fator_manual(self):
        # Regressao: o fluxo antigo (sem apresentacao, fator digitado a
        # mao) precisa continuar funcionando -- so mexi no form pra tornar
        # `fator_conversao` condicional, nao pra quebrar o caminho antigo.
        entrada = self.criar_entrada()
        response = self.post(entrada, {
            'produto': self.produto.pk,
            'quantidade': '10',
            'valor_unitario': '5.00',
            'fator_conversao': '2',
            'unidade_xml': 'CX',
        })
        self.assertEqual(response.status_code, 302)
        item = ItemEntradaNF.objects.get(entrada=entrada)
        self.assertIsNone(item.apresentacao)
        self.assertEqual(item.fator_conversao, Decimal('2.0000'))
        self.assertEqual(item.quantidade_estoque, Decimal('20.000'))

    def test_post_sem_apresentacao_e_sem_fator_e_rejeitado(self):
        entrada = self.criar_entrada()
        response = self.post(entrada, {
            'produto': self.produto.pk,
            'quantidade': '10',
            'valor_unitario': '5.00',
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ItemEntradaNF.objects.filter(entrada=entrada).exists())

    def test_post_com_apresentacao_de_outro_produto_e_rejeitado_no_form(self):
        outro_produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.un, descricao='Outro produto',
            codigo='1003', ncm='39235000',
        )
        ProdutoFilial.objects.create(produto=outro_produto, filial=self.filial)
        entrada = self.criar_entrada()
        response = self.post(entrada, {
            'produto': outro_produto.pk,
            'apresentacao': self.pacote500.pk,  # pertence a self.produto, nao a outro_produto
            'quantidade': '1',
            'valor_unitario': '10',
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ItemEntradaNF.objects.filter(entrada=entrada).exists())
