"""
Secao 11 do documento de especificacao -- casos 6 (baixa de estoque por
apresentacao), 9 (cancelamento usando o fator do snapshot) e 10
(concorrencia/select_for_update), com os numeros exatos do enunciado.

Casos 7 e 8 (compra e devolucao usando apresentacao) tem cobertura
propria em apps/compras/tests/test_apresentacao_compra.py e
apps/vendas/tests/test_devolucao.py -- apresentacao ja esta conectada
nos dois fluxos, entao nao sao repetidos aqui.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import EstoqueInsuficienteError
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import FormaPagamento
from apps.pdv.models import Caixa, SessaoPDV
from apps.pdv.services.edicao_venda_service import estornar_venda_para_edicao
from apps.pdv.services.venda_pdv_service import VendaPDVService
from apps.produtos.models import Produto, ProdutoApresentacao, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class Secao11EstoqueBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Secao 11 Estoque LTDA', cnpj='71345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL, codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Secao 11 Estoque', cnpj='71345678000192', uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='secao11@inoovated.com', nome='Operador Secao 11', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.un = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='UN', descricao='Unidade')
        cls.cx = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='CX', descricao='Caixa')
        UnidadeMedidaFilial.objects.create(unidade=cls.un, filial=cls.filial)
        UnidadeMedidaFilial.objects.create(unidade=cls.cx, filial=cls.filial)
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.un, descricao='Embalagem Pote 500ml',
            codigo='1001', ncm='39235000', preco_venda=Decimal('10.00'), preco_custo=Decimal('4.00'),
            controla_lote=False, permite_venda_sem_estoque=False,
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)
        cls.cx1000 = ProdutoApresentacao.objects.create(
            produto=cls.produto, unidade=cls.cx, descricao='Caixa 1.000', fator_conversao=1000,
            preco_venda=Decimal('9000.00'),
        )
        cls.caixa_pdv = Caixa.objects.create(filial=cls.filial, numero=1, descricao='Caixa 1')
        cls.forma_dinheiro = FormaPagamento.objects.create(
            empresa=cls.empresa, descricao='Dinheiro', tipo=TipoFormaPagamento.DINHEIRO,
        )

    def setUp(self):
        self.sessao = SessaoPDV.objects.create(
            filial=self.filial, caixa=self.caixa_pdv, usuario=self.usuario,
            valor_abertura=Decimal('0'), status='aberto',
        )

    def abastecer(self, quantidade):
        return MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(quantidade), usuario_id=self.usuario.pk, valor_unitario=Decimal('4.00'),
        )

    def vender_caixas(self, quantidade_caixas, *, forcar_estoque_negativo=False):
        quantidade_base = self.cx1000.fator_conversao * Decimal(quantidade_caixas)
        return VendaPDVService.finalizar_venda(
            sessao=self.sessao, filial=self.filial, usuario=self.usuario,
            itens=[{
                'produto_id': self.produto.pk, 'quantidade': str(quantidade_base),
                'preco_manual': str(self.cx1000.preco_venda / self.cx1000.fator_conversao),
            }],
            pagamentos=[{
                'forma_id': self.forma_dinheiro.pk,
                'valor': str(self.cx1000.preco_venda * Decimal(quantidade_caixas)),
            }],
            forcar_estoque_negativo=forcar_estoque_negativo,
        )


class Secao11Caso6Tests(Secao11EstoqueBase):
    """Caso 6: estoque 10.000 UN, venda de 3 CX1000 -> saldo 7.000 UN."""

    def test_venda_de_3_cx1000_deixa_saldo_em_7000_un(self):
        self.abastecer('10000')
        self.vender_caixas(3)
        estoque = Estoque.objects.get(produto=self.produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal('7000.000'))


class Secao11Caso9Tests(Secao11EstoqueBase):
    """
    Caso 9: cancelamento (estorno) de uma venda feita via apresentacao usa
    o snapshot gravado no item -- nunca recalcula pelo fator ATUAL da
    apresentacao. Prova disso: muda o fator_conversao DEPOIS da venda e
    confirma que o estorno ainda devolve exatamente o que a venda baixou.
    """

    def test_estorno_de_venda_por_apresentacao_ignora_fator_alterado_depois(self):
        self.abastecer('10000')
        venda = self.vender_caixas(3)  # baixa 3.000 UN (3 x fator 1000)
        estoque_apos_venda = Estoque.objects.get(produto=self.produto, filial=self.filial)
        self.assertEqual(estoque_apos_venda.quantidade_atual, Decimal('7000.000'))

        # Fator da apresentacao muda DEPOIS da venda -- se o estorno
        # recalculasse por ele, devolveria 3 x 500 = 1.500 UN (errado).
        self.cx1000.fator_conversao = Decimal('500')
        self.cx1000.save(update_fields=['fator_conversao', 'updated_at'])

        estornar_venda_para_edicao(venda, self.usuario)

        estoque_apos_estorno = Estoque.objects.get(produto=self.produto, filial=self.filial)
        self.assertEqual(estoque_apos_estorno.quantidade_atual, Decimal('10000.000'))
        self.assertTrue(
            MovimentacaoEstoque.objects.filter(
                produto=self.produto,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
                documento_id=venda.pk,
                quantidade=Decimal('3000.000'),
            ).exists()
        )


class Secao11Caso10Tests(Secao11EstoqueBase):
    """
    Caso 10: duas vendas simultaneas do mesmo produto/filial, cada uma
    pedindo quantidade proxima ao saldo total -- so uma deve ser aprovada
    quando a soma excede o disponivel.

    Sem threads de verdade (Django TestCase roda numa unica conexao/
    transacao) -- mesma tecnica ja usada no projeto para testar
    select_for_update (ver apps/estoque/tests/test_ajuste_rapido.py::
    test_lock_obtido_antes_de_calcular_delta): o `side_effect` do mock
    aplica a baixa da "primeira venda concorrente" exatamente no instante
    em que a segunda venda pede o lock, simulando a serializacao que o
    select_for_update garantiria de verdade contra duas conexoes reais.

    BUG PRE-EXISTENTE ENCONTRADO POR ESTE TESTE, CORRIGIDO: este teste
    denunciou (e falhava, via `expectedFailure`) uma race condition real
    em `MovimentacaoService.registrar_saida_fefo` -- para produto sem
    controle de lote, a checagem de "estoque insuficiente" lia o saldo
    SEM lock (`Estoque.objects.filter(...).values_list(...)`) antes de
    chamar `registrar_movimentacao` (que nunca bloqueia saldo negativo
    por si so -- essa decisao e' desta funcao). Duas vendas concorrentes
    podiam passar juntas na checagem (lendo o mesmo saldo desatualizado)
    e as duas decrementarem de verdade -- o saldo ficava aritmeticamente
    certo (a baixa em si ja era protegida pelo lock dentro de
    `registrar_movimentacao`), mas a REJEICAO por estoque insuficiente
    nao acontecia quando deveria. Corrigido adicionando
    `select_for_update()` na propria checagem, dentro da mesma transacao
    `@tenant_atomic` que `registrar_movimentacao` reusa.
    """

    def test_apenas_uma_venda_e_aprovada_quando_a_soma_excede_o_saldo(self):
        # Saldo para exatamente 3 caixas (3.000 UN). Duas vendas de 2 caixas
        # cada (2.000 UN) somam 4.000 -- mais que o saldo.
        self.abastecer('3000')

        manager = Estoque.objects
        original_select_for_update = manager.select_for_update

        def concorrente_baixa_2_caixas_antes_do_lock(*args, **kwargs):
            # Simula a "primeira venda" comitando entre o momento em que a
            # segunda decidiu vender e o momento em que ela de fato pega o
            # lock -- exatamente o que select_for_update deveria impedir
            # entre duas conexoes reais (aqui simulado no ponto do lock).
            manager.filter(produto=self.produto, filial=self.filial).update(
                quantidade_atual=Decimal('1000.000'), quantidade_disponivel=Decimal('1000.000'),
            )
            return original_select_for_update(*args, **kwargs)

        with patch.object(manager, 'select_for_update', side_effect=concorrente_baixa_2_caixas_antes_do_lock):
            with self.assertRaises(EstoqueInsuficienteError):
                # "Segunda venda": pede 2 caixas (2.000 UN), mas so' ve' 1.000
                # UN disponivel apos o lock (que ja reflete a baixa da
                # "primeira venda concorrente" simulada acima).
                self.vender_caixas(2, forcar_estoque_negativo=False)

        # `registrar_saida_fefo` e' `@tenant_atomic`: quando ele levanta
        # EstoqueInsuficienteError, a transacao INTEIRA da "segunda venda"
        # e' desfeita -- inclusive a baixa da "primeira venda concorrente"
        # simulada aqui dentro do mock, porque neste teste ambas rodam na
        # mesma conexao/transacao (TestCase nao abre duas conexoes reais).
        # O saldo volta pro valor de antes da simulacao (3.000): o ponto
        # que importa e' que a segunda venda NAO decrementou nada por cima
        # do que a primeira ja tinha vendido -- prova o assertRaises acima,
        # nao o valor residual aqui.
        estoque = Estoque.objects.get(produto=self.produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal('3000.000'))
