"""
Fase 7 do estoque por depósito: a carga da viagem sai do mesmo depósito de
onde o PDV vende, e o que volta (retorno de venda ambulante, bonificação
não entregue) retorna pra lá.

Sem depósito de venda separado, `Deposito.venda_id` cai no padrão e nada
muda -- por isso o resto de `test_estoque_viagem.py`/`test_entrega_
bonificacao.py` segue igual.
"""
from decimal import Decimal

from django.test import TestCase

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import Viagem
from apps.logistica.services.entrega_bonificacao import EntregaBonificacaoService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

ZERO = Decimal('0')


class ViagemDepositoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Viagem Depósito LTDA', nome_fantasia='ViagemDep',
            cnpj='41945678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='41945678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='doca@viagemdep.local', nome='Doca', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.remessa = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='remessa', descricao='Remessa',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.remessa, cfop='5904')
        cls.bonificacao = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='bonif', descricao='Bonificação',
            especie=NaturezaOperacao.Especie.BONIFICACAO,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.bonificacao, cfop='5910')

    def setUp(self):
        self.padrao_id = Deposito.padrao_id(self.filial.pk)
        Deposito.objects.filter(pk=self.padrao_id).update(permite_venda=False)
        self.loja = Deposito.objects.create(
            filial=self.filial, nome='Loja', tipo=Deposito.Tipo.REVENDA,
            permite_venda=True,
        )
        self.produto = self._produto('P1', '1000')

    def _produto(self, codigo, saldo):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade,
            descricao=f'Produto {codigo}', codigo=codigo, ncm='20079900',
            controla_lote=False, preco_venda=Decimal('10'),
            preco_custo=Decimal('4'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(saldo), usuario_id=self.usuario.pk,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
            deposito_id=self.loja.pk,
        )
        return produto

    def _viagem(self):
        return Viagem.objects.create(
            filial=self.filial, numero=Viagem.objects.count() + 1,
            motorista_nome='Seu Zé', veiculo_placa='ABC1D23',
            vendedor=self.usuario, responsavel=self.usuario,
        )

    def _saldo(self, deposito):
        return (
            Estoque.objects.filter(
                produto=self.produto, filial=self.filial, deposito=deposito,
            ).values_list('quantidade_atual', flat=True).first()
            or ZERO
        )


class SaidaDeCargaNoDepositoDeVendaTests(ViagemDepositoBase):

    def test_fechar_carga_baixa_do_deposito_de_venda(self):
        viagem = self._viagem()
        ViagemService.adicionar_item(viagem, {
            'natureza': self.remessa, 'produto': self.produto,
            'quantidade': '300', 'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)

        self.assertEqual(self._saldo(self.loja), Decimal('700.000'))
        self.assertFalse(
            Estoque.objects.filter(
                produto=self.produto, filial=self.filial,
                deposito_id=self.padrao_id,
            ).exists()
        )  # padrão nunca teve saldo -- não foi tocado
        mov = MovimentacaoEstoque.objects.get(
            documento_tipo='viagem', documento_id=viagem.pk,
        )
        self.assertEqual(mov.deposito_id, self.loja.pk)


class RetornoDeVendaAmbulanteVoltaParaODepositoTests(ViagemDepositoBase):

    def test_retorno_da_viagem_volta_para_o_deposito_de_venda(self):
        viagem = self._viagem()
        ViagemService.adicionar_item(viagem, {
            'natureza': self.remessa, 'produto': self.produto,
            'quantidade': '300', 'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])

        ViagemService.registrar_retorno(
            viagem, self.produto, Decimal('50'), usuario=self.usuario,
        )

        # saiu 300, voltou 50: líquido -250 no depósito de venda
        self.assertEqual(self._saldo(self.loja), Decimal('750.000'))
        mov = MovimentacaoEstoque.objects.get(
            documento_tipo='viagem_retorno', documento_id=viagem.pk,
        )
        self.assertEqual(mov.deposito_id, self.loja.pk)


class RetornoDeBonificacaoVoltaParaODepositoDaSaidaTests(ViagemDepositoBase):

    def test_bonificacao_nao_entregue_volta_para_o_deposito_que_saiu(self):
        from apps.logistica.models import EntregaBonificacao

        viagem = self._viagem()
        item = ViagemService.adicionar_item(viagem, {
            'natureza': self.bonificacao, 'produto': self.produto,
            'cliente': self.cliente, 'quantidade': '20',
            'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        self.assertEqual(self._saldo(self.loja), Decimal('980.000'))

        entrega = EntregaBonificacaoService.para_item(item)
        S = EntregaBonificacao.Status
        EntregaBonificacaoService.mover(entrega, S.EM_TRANSPORTE)
        EntregaBonificacaoService.mover(entrega, S.RECUSADA, {
            'motivo_nao_entrega': EntregaBonificacao.MotivoNaoEntrega.AUSENTE,
        })
        EntregaBonificacaoService.mover(entrega, S.RETORNO_PENDENTE)
        EntregaBonificacaoService.tratar_retorno(entrega, usuario=self.usuario)

        self.assertEqual(self._saldo(self.loja), Decimal('1000.000'))
        devolucao = MovimentacaoEstoque.objects.get(
            documento_tipo='viagem', documento_id=viagem.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
        )
        self.assertEqual(devolucao.deposito_id, self.loja.pk)
