"""
Fase 4 do estoque por depósito: a venda (PDV) tira mercadoria só de
depósito com `permite_venda`. Sem separar depósitos, `Deposito.venda_id`
devolve o padrão e nada muda.
"""
from decimal import Decimal

from django.test import TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import FormaPagamento
from apps.pdv.models import Caixa, SessaoPDV
from apps.pdv.services.produto_vendavel_service import ProdutoVendavelService
from apps.pdv.services.venda_pdv_service import VendaPDVService
from apps.produtos.models import (
    Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial,
)


class VendaNoDepositoTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='PDV Dep LTDA', nome_fantasia='PDVDep',
            cnpj='39345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='F', nome_fantasia='Matriz',
            cnpj='39345678000192', uf='RN', is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Op', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='pdvdep@teste.local', nome='Op', password='x' * 12,
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.caixa = Caixa.objects.create(filial=cls.filial, numero=1, descricao='Cx 1')
        cls.forma = FormaPagamento.objects.create(
            empresa=cls.empresa, descricao='Dinheiro',
            tipo=TipoFormaPagamento.DINHEIRO,
        )
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade, descricao='Bola',
            ncm='95066200', controla_lote=False,
            permite_venda_sem_estoque=False,
            preco_venda=Decimal('10.00'), preco_custo=Decimal('4.00'),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)

    def setUp(self):
        self.sessao = SessaoPDV.objects.create(
            filial=self.filial, caixa=self.caixa, usuario=self.usuario,
            valor_abertura=Decimal('0.00'), status='aberto',
        )
        # padrão vira "não-venda", uma Loja separada é o depósito de venda
        self.padrao_id = Deposito.padrao_id(self.filial.pk)
        Deposito.objects.filter(pk=self.padrao_id).update(permite_venda=False)
        self.loja = Deposito.objects.create(
            filial=self.filial, nome='Loja', tipo=Deposito.Tipo.REVENDA,
            permite_venda=True,
        )

    def _abastecer(self, deposito_id, qtd):
        MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(qtd), usuario_id=self.usuario.pk,
            valor_unitario=Decimal('4.00'), deposito_id=deposito_id,
        )

    def test_venda_id_aponta_para_a_loja_quando_o_padrao_nao_vende(self):
        self.assertEqual(Deposito.venda_id(self.filial.pk), self.loja.pk)

    def test_pdv_baixa_o_estoque_da_loja_e_nao_do_padrao(self):
        self._abastecer(self.loja.pk, '5')
        self._abastecer(self.padrao_id, '99')  # estoque de fábrica, não some

        VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{'produto_id': self.produto.pk, 'quantidade': '2'}],
            pagamentos=[{'forma_id': self.forma.pk, 'valor': '20'}],
        )

        loja = Estoque.objects.get(
            produto=self.produto, filial=self.filial, deposito=self.loja,
        )
        self.assertEqual(loja.quantidade_atual, Decimal('3.000'))
        padrao = Estoque.objects.get(
            produto=self.produto, filial=self.filial, deposito_id=self.padrao_id,
        )
        self.assertEqual(padrao.quantidade_atual, Decimal('99.000'))

    def test_disponivel_no_pdv_reflete_so_o_deposito_de_venda(self):
        self._abastecer(self.loja.pk, '7')
        self._abastecer(self.padrao_id, '40')

        info = ProdutoVendavelService.consultar(
            produto=self.produto, filial=self.filial, quantidade=Decimal('1'),
        )
        self.assertEqual(Decimal(str(info['saldo_disponivel'])), Decimal('7.000'))
