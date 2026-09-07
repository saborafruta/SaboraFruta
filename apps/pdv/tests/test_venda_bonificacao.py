"""
Venda em bonificação — saída sem cobrança, com CFOP de bonificação na nota.

O QUE ESTES TESTES CERCAM:

  · NÃO PASSA POR PAGAMENTO. Diferente de uma venda normal, valor_total > 0
    sem nenhum `pagamentos` não bloqueia — é o ponto inteiro de existir;

  · NÃO ENTRA NO CAIXA NEM GERA CONTA A RECEBER. Mesmo tratamento que
    Doação/Permuta já davam a uma forma de pagamento isolada;

  · BAIXA ESTOQUE NORMALMENTE. A mercadoria sai de verdade — só não é
    cobrada;

  · EXIGE CLIENTE. Bonificação é dirigida a alguém, não ao Consumidor
    Final do balcão;

  · O CFOP DA NOTA é 5910/6910 (bonificação), não 5102/6102 (venda) — e a
    natureza da operação diz "BONIFICAÇÃO", não "VENDA...".
"""
from decimal import Decimal

from django.test import TestCase

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import Estoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.estoque.models import MovimentacaoEstoque
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import FormaPagamento
from apps.pdv.models import Caixa, ItemVendaPDV, SessaoPDV, VendaPDV
from apps.pdv.services.nfce_payload_builder import NfePayloadBuilder, NfcePayloadBuilder
from apps.pdv.services.venda_pdv_service import VendaPDVService
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class BonificacaoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Bonificacao LTDA', nome_fantasia='Bonificacao',
            cnpj='62345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Bonificacao', nome_fantasia='Matriz',
            cnpj='62345678000192', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Operador PDV', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='bonificacao@inoovated.com', nome='Usuario Bonificacao',
            password='teste1234', empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.caixa = Caixa.objects.create(filial=cls.filial, numero=1, descricao='Caixa 1')
        cls.forma = FormaPagamento.objects.create(
            empresa=cls.empresa, descricao='Dinheiro', tipo=TipoFormaPagamento.DINHEIRO,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado Parceiro',
            cpf_cnpj='12345678000190', ativo=True,
            endereco='Rua das Polpas', numero='100', bairro='Centro',
            cidade='Natal', uf='RN', cep='59000000',
            codigo_municipio_ibge='2408102',
        )

    def setUp(self):
        self.sessao = SessaoPDV.objects.create(
            filial=self.filial, caixa=self.caixa, usuario=self.usuario,
            valor_abertura=Decimal('0.00'), status='aberto',
        )

    def criar_produto(self, descricao='Polpa Bonificação'):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade, descricao=descricao,
            ncm='20089900', controla_lote=False, permite_venda_sem_estoque=False,
            preco_venda=Decimal('10.00'), preco_custo=Decimal('4.00'),
            cfop_venda_interna='5102',
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        return produto

    def abastecer(self, produto, quantidade='10'):
        return MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(quantidade), usuario_id=self.usuario.pk,
            valor_unitario=Decimal('4.00'),
        )


class FinalizarBonificacaoTests(BonificacaoBase):

    def test_finaliza_sem_pagamento_nenhum(self):
        produto = self.criar_produto()
        self.abastecer(produto, '10')

        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{'produto_id': produto.pk, 'quantidade': '2'}],
            pagamentos=[],
            cliente_id=self.cliente.pk,
            bonificacao=True,
        )

        self.assertTrue(venda.bonificacao)
        self.assertEqual(venda.valor_total, Decimal('20.00'))
        self.assertEqual(venda.valor_pago, Decimal('20.00'))
        self.assertEqual(venda.pagamentos.count(), 0)

    def test_nao_conta_no_caixa(self):
        produto = self.criar_produto()
        self.abastecer(produto, '10')
        total_antes = self.sessao.total_vendas or Decimal('0')

        VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{'produto_id': produto.pk, 'quantidade': '2'}],
            pagamentos=[],
            cliente_id=self.cliente.pk,
            bonificacao=True,
        )

        self.sessao.refresh_from_db()
        self.assertEqual(self.sessao.total_vendas or Decimal('0'), total_antes)

    def test_baixa_estoque_normalmente(self):
        produto = self.criar_produto()
        self.abastecer(produto, '10')

        VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{'produto_id': produto.pk, 'quantidade': '3'}],
            pagamentos=[],
            cliente_id=self.cliente.pk,
            bonificacao=True,
        )

        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal('7.000'))

    def test_exige_cliente(self):
        produto = self.criar_produto()
        self.abastecer(produto, '10')

        with self.assertRaises(DadosInvalidosError):
            VendaPDVService.finalizar_venda(
                sessao=self.sessao, filial=self.filial, usuario=self.usuario,
                itens=[{'produto_id': produto.pk, 'quantidade': '1'}],
                pagamentos=[],
                cliente_id=None,
                bonificacao=True,
            )

    def test_venda_normal_continua_exigindo_pagamento(self):
        """
        A flag é opt-in — sem ela, o comportamento de sempre continua
        cobrando pelo total.
        """
        produto = self.criar_produto()
        self.abastecer(produto, '10')

        with self.assertRaises(DadosInvalidosError):
            VendaPDVService.finalizar_venda(
                sessao=self.sessao, filial=self.filial, usuario=self.usuario,
                itens=[{'produto_id': produto.pk, 'quantidade': '1'}],
                pagamentos=[],
            )


class NotaFiscalBonificacaoTests(BonificacaoBase):
    """O CFOP e a natureza da operação saem certos na nota."""

    def _venda_bonificada(self):
        produto = self.criar_produto()
        self.abastecer(produto, '10')
        return VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{'produto_id': produto.pk, 'quantidade': '2'}],
            pagamentos=[],
            cliente_id=self.cliente.pk,
            bonificacao=True,
        )

    def test_nfce_sai_com_cfop_de_bonificacao_e_natureza_certa(self):
        venda = self._venda_bonificada()

        payload = NfcePayloadBuilder.build(venda, numero=1, serie=1)

        self.assertEqual(payload['natureza_operacao'], 'BONIFICAÇÃO')
        self.assertEqual(payload['items'][0]['cfop'], '5910')

    def test_nfe_sai_com_cfop_de_bonificacao_e_natureza_certa(self):
        venda = self._venda_bonificada()

        payload = NfePayloadBuilder.build(venda, numero_nfe=1, serie_nfe=1)

        self.assertEqual(payload['natureza_operacao'], 'BONIFICAÇÃO')
        self.assertEqual(payload['items'][0]['cfop'], '5910')

    def test_venda_normal_continua_com_o_cfop_de_venda(self):
        produto = self.criar_produto()
        self.abastecer(produto, '10')
        venda = VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{'produto_id': produto.pk, 'quantidade': '1'}],
            pagamentos=[{'forma_id': self.forma.pk, 'valor': '10.00'}],
        )

        payload = NfcePayloadBuilder.build(venda, numero=2, serie=1)

        self.assertEqual(payload['natureza_operacao'], 'VENDA AO CONSUMIDOR')
        self.assertEqual(payload['items'][0]['cfop'], '5102')
