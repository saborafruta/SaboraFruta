"""
Pré-venda — o vendedor externo registra a intenção, sem caixa e sem pagar.

O QUE ESTES TESTES CERCAM:

  · NÃO EXIGE CAIXA ABERTO. Diferente de "Salvar Pendente" (que já existia
    dentro do PDV), quem está batendo porta não tem um caixa físico atrás;

  · CAI NA MESMA FILA DE PENDENTES do PDV -- não é uma consulta paralela.
    `api_pendentes` já filtra só por filial, nunca por sessão, então uma
    pré-venda sem caixa nenhum aparece lá do mesmo jeito;

  · NÃO MEXE EM ESTOQUE NA CRIAÇÃO -- só na finalização, quando alguém
    retoma pela tela normal do PDV e escolhe a forma de pagamento;

  · EXIGE CLIENTE -- é dirigido a quem o vendedor visitou, não ao balcão.
"""
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class PreVendaBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Pre Venda LTDA', nome_fantasia='Pre Venda',
            cnpj='72345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Pre Venda', nome_fantasia='Matriz',
            cnpj='72345678000192', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Vendedor Externo', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='vendedor-externo@inoovated.com', nome='Vendedor Externo',
            password='teste1234', empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Cliente Visitado',
            cpf_cnpj='12345678000190', ativo=True,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

    def criar_produto(self, descricao='Produto Pré-venda'):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade, descricao=descricao,
            ncm='20089900', controla_lote=False, permite_venda_sem_estoque=False,
            preco_venda=Decimal('10.00'), preco_custo=Decimal('4.00'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        return produto

    def post_json(self, url_name, payload):
        return self.client.post(
            reverse(f'pdv:{url_name}'), data=json.dumps(payload),
            content_type='application/json',
        )


class TelaTests(PreVendaBase):

    def test_a_tela_abre_sem_caixa(self):
        resposta = self.client.get(reverse('pdv:pre_venda_nova'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Nova Pré-venda')
        self.assertContains(resposta, 'Sem caixa, sem pagamento agora')


class CriarPreVendaTests(PreVendaBase):

    def test_cria_pendente_sem_sessao_de_caixa(self):
        produto = self.criar_produto()

        resposta = self.post_json('api_pre_venda_criar', {
            'cliente_id': self.cliente.pk,
            'itens': [{'produto_id': produto.pk, 'quantidade': 2, 'valor_unitario': 10}],
        })

        self.assertEqual(resposta.status_code, 200, resposta.content)
        dados = resposta.json()
        self.assertTrue(dados['ok'])

        venda = VendaPDV.objects.get(pk=dados['venda_id'])
        self.assertIsNone(venda.sessao_pdv_id)
        self.assertEqual(venda.status, 'aberta')
        self.assertEqual(venda.origem, 'pre_venda')
        self.assertEqual(venda.cliente_id, self.cliente.pk)
        self.assertEqual(venda.valor_total, Decimal('20.00'))

    def test_nao_mexe_em_estoque(self):
        produto = self.criar_produto()
        from apps.estoque.services.movimentacao_service import MovimentacaoService
        from apps.estoque.models import MovimentacaoEstoque
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('10'), usuario_id=self.usuario.pk,
            valor_unitario=Decimal('4.00'),
        )

        self.post_json('api_pre_venda_criar', {
            'cliente_id': self.cliente.pk,
            'itens': [{'produto_id': produto.pk, 'quantidade': 3, 'valor_unitario': 10}],
        })

        estoque = Estoque.objects.get(produto=produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal('10.000'))

    def test_exige_cliente(self):
        produto = self.criar_produto()

        resposta = self.post_json('api_pre_venda_criar', {
            'itens': [{'produto_id': produto.pk, 'quantidade': 1, 'valor_unitario': 10}],
        })

        self.assertEqual(resposta.status_code, 400)
        self.assertIn('cliente', resposta.json()['erro'].lower())
        self.assertEqual(VendaPDV.objects.count(), 0)

    def test_exige_itens(self):
        resposta = self.post_json('api_pre_venda_criar', {'cliente_id': self.cliente.pk, 'itens': []})

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(VendaPDV.objects.count(), 0)

    def test_sem_permissao_de_pdv_nao_cria(self):
        self.usuario.perfil.is_admin = False
        self.usuario.perfil.save(update_fields=['is_admin'])
        produto = self.criar_produto()

        resposta = self.post_json('api_pre_venda_criar', {
            'cliente_id': self.cliente.pk,
            'itens': [{'produto_id': produto.pk, 'quantidade': 1, 'valor_unitario': 10}],
        })

        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(VendaPDV.objects.count(), 0)


class IntegracaoComPendentesTests(PreVendaBase):
    """A pré-venda precisa aparecer na MESMA fila que o PDV já usa."""

    def test_a_pre_venda_aparece_em_pendentes(self):
        produto = self.criar_produto()
        criada = self.post_json('api_pre_venda_criar', {
            'cliente_id': self.cliente.pk,
            'itens': [{'produto_id': produto.pk, 'quantidade': 1, 'valor_unitario': 10}],
        }).json()

        resposta = self.client.get(reverse('pdv:api_pendentes'))

        ids = [p['id'] for p in resposta.json()['pendentes']]
        self.assertIn(criada['venda_id'], ids)

    def test_o_detalhe_da_pre_venda_abre_pela_tela_normal_do_pdv(self):
        produto = self.criar_produto()
        criada = self.post_json('api_pre_venda_criar', {
            'cliente_id': self.cliente.pk,
            'itens': [{'produto_id': produto.pk, 'quantidade': 2, 'valor_unitario': 10}],
        }).json()

        resposta = self.client.get(
            reverse('pdv:api_pendente_detalhe', args=[criada['venda_id']])
        )

        self.assertEqual(resposta.status_code, 200)
        dados = resposta.json()
        self.assertEqual(len(dados['itens']), 1)
        self.assertEqual(ItemVendaPDV.objects.filter(venda_pdv_id=criada['venda_id']).count(), 1)
