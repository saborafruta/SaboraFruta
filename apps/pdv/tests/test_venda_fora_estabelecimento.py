"""
Venda fora do estabelecimento — venda normal, só muda o CFOP na nota.

O QUE ESTES TESTES CERCAM:

  · É VENDA NORMAL. Mesmo pagamento, mesmo caixa, mesma conta a receber --
    diferente da Bonificação, esta flag não pula nada do fluxo de sempre;

  · O CFOP DA NOTA é 5103/6103 ("venda de produção do estabelecimento,
    efetuada fora do estabelecimento"), não 5102/6102 (venda comum) -- e a
    natureza da operação diz "VENDA FORA DO ESTABELECIMENTO";

  · O INDICADOR DE PRESENÇA (indPres) é "5" (presencial fora do
    estabelecimento) -- "1" declararia uma venda de balcão que não
    aconteceu no balcão;

  · BONIFICAÇÃO E VENDA FORA NÃO COEXISTEM na mesma nota -- são naturezas
    de saída que se excluem.
"""
from decimal import Decimal

from django.test import TestCase

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import FormaPagamento
from apps.pdv.models import Caixa, SessaoPDV
from apps.pdv.services.nfce_payload_builder import NfePayloadBuilder, NfcePayloadBuilder
from apps.pdv.services.venda_pdv_service import VendaPDVService
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class VendaForaEstabelecimentoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Venda Fora LTDA', nome_fantasia='Venda Fora',
            cnpj='72345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Venda Fora', nome_fantasia='Matriz',
            cnpj='72345678000192', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Operador PDV', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='vendafora@inoovated.com', nome='Usuario Venda Fora',
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
            filial=cls.filial, razao_social='Comprador da Rota',
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

    def criar_produto(self, descricao='Polpa Venda Fora'):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade, descricao=descricao,
            ncm='20089900', controla_lote=False, permite_venda_sem_estoque=False,
            preco_venda=Decimal('10.00'), preco_custo=Decimal('4.00'),
            cfop_venda_interna='5102', cfop_venda_interestadual='6102',
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        return produto

    def _finalizar(self, produto, **kw):
        dados = dict(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{'produto_id': produto.pk, 'quantidade': '2'}],
            pagamentos=[{'forma_id': self.forma.pk, 'valor': '20.00'}],
        )
        dados.update(kw)
        return VendaPDVService.finalizar_venda(**dados)


class FinalizarVendaForaTests(VendaForaEstabelecimentoBase):

    def test_e_venda_normal_com_pagamento_e_caixa(self):
        """Diferente da bonificação, esta flag não muda o fluxo de cobrança."""
        produto = self.criar_produto()

        venda = self._finalizar(produto, venda_fora_estabelecimento=True)

        self.assertTrue(venda.venda_fora_estabelecimento)
        self.assertEqual(venda.valor_total, Decimal('20.00'))
        self.assertEqual(venda.pagamentos.count(), 1)

    def test_entra_no_caixa_normalmente(self):
        produto = self.criar_produto()
        total_antes = self.sessao.total_vendas or Decimal('0')

        self._finalizar(produto, venda_fora_estabelecimento=True)

        self.sessao.refresh_from_db()
        self.assertEqual(
            self.sessao.total_vendas or Decimal('0'),
            total_antes + Decimal('20.00'),
        )

    def test_sem_pagamento_continua_recusando(self):
        """Não é bonificação -- ainda exige o pagamento cobrir o total."""
        produto = self.criar_produto()

        with self.assertRaises(DadosInvalidosError):
            self._finalizar(produto, pagamentos=[], venda_fora_estabelecimento=True)

    def test_bonificacao_e_venda_fora_nao_coexistem(self):
        produto = self.criar_produto()

        with self.assertRaises(DadosInvalidosError):
            self._finalizar(
                produto, pagamentos=[], cliente_id=self.cliente.pk,
                bonificacao=True, venda_fora_estabelecimento=True,
            )

    def test_flag_padrao_e_false(self):
        produto = self.criar_produto()

        venda = self._finalizar(produto)

        self.assertFalse(venda.venda_fora_estabelecimento)


class NotaFiscalVendaForaTests(VendaForaEstabelecimentoBase):
    """O CFOP, a natureza da operação e o indicador de presença saem certos."""

    def _venda_fora(self):
        produto = self.criar_produto()
        return self._finalizar(
            produto, venda_fora_estabelecimento=True, cliente_id=self.cliente.pk,
        )

    def test_nfce_sai_com_cfop_5103_e_natureza_certa(self):
        venda = self._venda_fora()

        payload = NfcePayloadBuilder.build(venda, numero=1, serie=1)

        self.assertEqual(payload['natureza_operacao'], 'VENDA FORA DO ESTABELECIMENTO')
        self.assertEqual(payload['items'][0]['cfop'], '5103')
        self.assertEqual(payload['presenca_comprador'], '5')

    def test_nfe_sai_com_cfop_5103_e_natureza_certa(self):
        venda = self._venda_fora()

        payload = NfePayloadBuilder.build(venda, numero_nfe=1, serie_nfe=1)

        self.assertEqual(payload['natureza_operacao'], 'VENDA FORA DO ESTABELECIMENTO')
        self.assertEqual(payload['items'][0]['cfop'], '5103')
        self.assertEqual(payload['presenca_comprador'], '5')

    def test_venda_normal_continua_com_o_cfop_de_venda(self):
        produto = self.criar_produto()
        venda = self._finalizar(produto)

        payload = NfcePayloadBuilder.build(venda, numero=2, serie=1)

        self.assertEqual(payload['natureza_operacao'], 'VENDA AO CONSUMIDOR')
        self.assertEqual(payload['items'][0]['cfop'], '5102')
        self.assertEqual(payload['presenca_comprador'], '1')
