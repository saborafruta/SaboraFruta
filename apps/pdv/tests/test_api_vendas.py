"""POST /api/pdv/vendas/ (Fase 15, ver apps/pdv/api/views.py)."""
import json
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import Empresa, Filial, PerfilAcesso, Permissao, Usuario
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import FormaPagamento
from apps.pdv.models import Caixa, SessaoPDV, VendaPDV
from apps.produtos.models import (
    Produto, ProdutoApresentacao, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial,
)


class ApiVendaPDVBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Rede API Vendas LTDA', nome_fantasia='Rede API Vendas',
            cnpj='91145678000191', regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Loja A', nome_fantasia='Loja A',
            cnpj='91145678000192', uf='RN', is_matriz=True,
        )
        cls.perfil_admin = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='caixa@inoovated.com', nome='Operador Caixa', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil_admin,
        )
        cls.perfil_sem_permissao = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Sem Permissao', is_admin=False,
        )
        Permissao.objects.create(perfil=cls.perfil_sem_permissao, modulo='pdv')
        cls.usuario_sem_permissao = Usuario.objects.create_user(
            email='sempermissao-pdv@inoovated.com', nome='Sem Permissao', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil_sem_permissao,
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
        cls.apresentacao_caixa = ProdutoApresentacao.objects.create(
            produto=cls.produto, unidade=cls.cx, descricao='Caixa 10', fator_conversao=10,
            preco_venda=Decimal('90.00'),
        )
        cls.caixa_pdv = Caixa.objects.create(filial=cls.filial, numero=1, descricao='Caixa 1')
        cls.forma_dinheiro = FormaPagamento.objects.create(
            empresa=cls.empresa, descricao='Dinheiro', tipo=TipoFormaPagamento.DINHEIRO,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_login(self.usuario)
        self.sessao = SessaoPDV.objects.create(
            filial=self.filial, caixa=self.caixa_pdv, usuario=self.usuario,
            valor_abertura=Decimal('0'), status='aberto',
        )

    def abastecer(self, quantidade='100'):
        return MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(quantidade), usuario_id=self.usuario.pk, valor_unitario=Decimal('4.00'),
        )

    def criar_venda(self, payload, idempotency_key=None):
        headers = {}
        if idempotency_key:
            headers['HTTP_IDEMPOTENCY_KEY'] = idempotency_key
        return self.client.post(
            reverse('pdv_api:vendas'), data=json.dumps(payload), content_type='application/json', **headers,
        )


class ApiVendaPDVAutenticacaoTests(ApiVendaPDVBase):
    def test_sem_login_retorna_401_ou_403(self):
        self.client.logout()
        response = self.criar_venda({'itens': [], 'pagamentos': []})
        self.assertIn(response.status_code, (401, 403))

    def test_sem_permissao_retorna_403(self):
        self.client.force_login(self.usuario_sem_permissao)
        response = self.criar_venda({'itens': [], 'pagamentos': []})
        self.assertEqual(response.status_code, 403)


class ApiVendaPDVCriacaoTests(ApiVendaPDVBase):
    def test_venda_simples_em_unidade_base(self):
        self.abastecer('100')
        response = self.criar_venda({
            'itens': [{'produto_id': self.produto.pk, 'quantidade': '2'}],
            'pagamentos': [{'forma_id': self.forma_dinheiro.pk, 'valor': '20.00'}],
        })
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['valor_total'], '20.00')
        self.assertEqual(len(response.data['itens']), 1)
        self.assertEqual(response.data['itens'][0]['quantidade'], '2.000')

        estoque = Estoque.objects.get(produto=self.produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal('98.000'))

    def test_venda_por_apresentacao_converte_para_unidade_base(self):
        self.abastecer('100')
        response = self.criar_venda({
            'itens': [{
                'produto_id': self.produto.pk,
                'apresentacao_id': self.apresentacao_caixa.pk,
                'quantidade_comercial': '2',
            }],
            'pagamentos': [{'forma_id': self.forma_dinheiro.pk, 'valor': '180.00'}],
        })
        self.assertEqual(response.status_code, 201, response.data)
        # 2 caixas de 10 = 20 unidades base baixadas do estoque.
        self.assertEqual(response.data['itens'][0]['quantidade'], '20.000')
        estoque = Estoque.objects.get(produto=self.produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal('80.000'))
        # Preco cobrado deriva do preco da APRESENTACAO (90/caixa de 10 =
        # 9,00 por UN base), nao do preco do produto na unidade base
        # (10/UN, que daria 200 no total, nao 180) -- regra 5.8.
        self.assertEqual(response.data['itens'][0]['valor_unitario'], '9.0000')
        self.assertEqual(response.data['valor_total'], '180.00')

    def test_apresentacao_de_outro_produto_e_rejeitada(self):
        outro_produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.un, descricao='Outro produto',
            codigo='1002', ncm='39235000',
        )
        ProdutoFilial.objects.create(produto=outro_produto, filial=self.filial)
        response = self.criar_venda({
            'itens': [{
                'produto_id': outro_produto.pk,
                'apresentacao_id': self.apresentacao_caixa.pk,  # pertence a self.produto, nao a outro_produto
                'quantidade_comercial': '1',
            }],
            'pagamentos': [],
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('erros', response.data)

    def test_apresentacao_que_nao_permite_venda_e_rejeitada(self):
        self.apresentacao_caixa.permite_venda = False
        self.apresentacao_caixa.save(update_fields=['permite_venda'])
        response = self.criar_venda({
            'itens': [{
                'produto_id': self.produto.pk,
                'apresentacao_id': self.apresentacao_caixa.pk,
                'quantidade_comercial': '1',
            }],
            'pagamentos': [],
        })
        self.assertEqual(response.status_code, 400)

    def test_estoque_insuficiente_com_forcar_estoque_negativo_false_e_rejeitada(self):
        self.abastecer('1')
        response = self.criar_venda({
            'itens': [{'produto_id': self.produto.pk, 'quantidade': '5'}],
            'pagamentos': [{'forma_id': self.forma_dinheiro.pk, 'valor': '50.00'}],
            'forcar_estoque_negativo': False,
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['erros'][0]['codigo'], 'estoque_insuficiente')

    def test_estoque_insuficiente_por_padrao_ainda_registra_a_venda(self):
        # Mesmo comportamento do fluxo HTML existente (forcar_estoque_negativo
        # default True): a venda passa e o estoque fica negativo.
        self.abastecer('1')
        response = self.criar_venda({
            'itens': [{'produto_id': self.produto.pk, 'quantidade': '5'}],
            'pagamentos': [{'forma_id': self.forma_dinheiro.pk, 'valor': '50.00'}],
        })
        self.assertEqual(response.status_code, 201, response.data)
        estoque = Estoque.objects.get(produto=self.produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal('-4.000'))

    def test_carrinho_vazio_e_rejeitado(self):
        response = self.criar_venda({'itens': [], 'pagamentos': []})
        self.assertEqual(response.status_code, 400)

    def test_sem_sessao_aberta_retorna_erro(self):
        self.sessao.status = 'fechado'
        self.sessao.save(update_fields=['status'])
        self.abastecer('10')
        response = self.criar_venda({
            'itens': [{'produto_id': self.produto.pk, 'quantidade': '1'}],
            'pagamentos': [{'forma_id': self.forma_dinheiro.pk, 'valor': '10.00'}],
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('sessao de caixa', response.data['erros'][0]['mensagem'])

    def test_desconto_e_observacao_passam_para_a_venda(self):
        self.abastecer('10')
        response = self.criar_venda({
            'itens': [{'produto_id': self.produto.pk, 'quantidade': '1'}],
            'pagamentos': [{'forma_id': self.forma_dinheiro.pk, 'valor': '9.00'}],
            'desconto': '1.00', 'observacao': 'Cliente pediu desconto',
        })
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['valor_desconto'], '1.00')
        self.assertEqual(response.data['observacao'], 'Cliente pediu desconto')


class ApiVendaPDVIdempotenciaTests(ApiVendaPDVBase):
    def test_reenvio_com_mesma_chave_retorna_a_mesma_venda(self):
        self.abastecer('10')
        payload = {
            'itens': [{'produto_id': self.produto.pk, 'quantidade': '1'}],
            'pagamentos': [{'forma_id': self.forma_dinheiro.pk, 'valor': '10.00'}],
        }
        primeira = self.criar_venda(payload, idempotency_key='pedido-abc-123')
        self.assertEqual(primeira.status_code, 201)

        segunda = self.criar_venda(payload, idempotency_key='pedido-abc-123')
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(segunda.data['id'], primeira.data['id'])

        # So uma venda foi criada de verdade -- e so uma baixa de estoque.
        self.assertEqual(VendaPDV.objects.filter(numero_venda=primeira.data['numero_venda']).count(), 1)
        estoque = Estoque.objects.get(produto=self.produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal('9.000'))

    def test_chaves_diferentes_criam_vendas_diferentes(self):
        self.abastecer('10')
        payload = {
            'itens': [{'produto_id': self.produto.pk, 'quantidade': '1'}],
            'pagamentos': [{'forma_id': self.forma_dinheiro.pk, 'valor': '10.00'}],
        }
        primeira = self.criar_venda(payload, idempotency_key='chave-1')
        segunda = self.criar_venda(payload, idempotency_key='chave-2')
        self.assertNotEqual(primeira.data['id'], segunda.data['id'])

    def test_sem_chave_cada_chamada_cria_uma_venda(self):
        self.abastecer('10')
        payload = {
            'itens': [{'produto_id': self.produto.pk, 'quantidade': '1'}],
            'pagamentos': [{'forma_id': self.forma_dinheiro.pk, 'valor': '10.00'}],
        }
        primeira = self.criar_venda(payload)
        segunda = self.criar_venda(payload)
        self.assertEqual(primeira.status_code, 201)
        self.assertEqual(segunda.status_code, 201)
        self.assertNotEqual(primeira.data['id'], segunda.data['id'])


@override_settings(PDV_VENDA_RATE_LIMIT=2)
class ApiVendaPDVThrottlingTests(ApiVendaPDVBase):
    """Fase 18: throttling dedicado em POST /vendas/."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def test_throttle_bloqueia_apos_o_limite(self):
        self.abastecer('10')
        payload = {
            'itens': [{'produto_id': self.produto.pk, 'quantidade': '1'}],
            'pagamentos': [{'forma_id': self.forma_dinheiro.pk, 'valor': '10.00'}],
        }
        self.assertEqual(self.criar_venda(payload).status_code, 201)
        self.assertEqual(self.criar_venda(payload).status_code, 201)
        terceira = self.criar_venda(payload)
        self.assertEqual(terceira.status_code, 429)

    def test_throttle_e_por_usuario_nao_global(self):
        self.abastecer('10')
        payload = {
            'itens': [{'produto_id': self.produto.pk, 'quantidade': '1'}],
            'pagamentos': [{'forma_id': self.forma_dinheiro.pk, 'valor': '10.00'}],
        }
        self.assertEqual(self.criar_venda(payload).status_code, 201)
        self.assertEqual(self.criar_venda(payload).status_code, 201)
        self.assertEqual(self.criar_venda(payload).status_code, 429)

        outro_perfil = PerfilAcesso.objects.create(empresa=self.empresa, nome='Admin 2', is_admin=True)
        outro_usuario = Usuario.objects.create_user(
            email='outro-caixa-throttle@inoovated.com', nome='Outro Operador', password='teste1234',
            empresa=self.empresa, filial=self.filial, perfil=outro_perfil,
        )
        SessaoPDV.objects.create(
            filial=self.filial, caixa=self.caixa_pdv, usuario=outro_usuario,
            valor_abertura=Decimal('0'), status='aberto',
        )
        self.client.force_login(outro_usuario)
        # Usuario diferente, cota propria -- nao afetado pelo throttle do primeiro.
        self.assertEqual(self.criar_venda(payload).status_code, 201)
