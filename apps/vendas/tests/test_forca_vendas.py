"""Fluxo web da força de vendas: pedido, Kanban e separação."""

import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.modulos import modulo_da_url, modulos_ativos
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.pdv.models import VendaPDV
from apps.produtos.models import (
    ItemTabelaPreco,
    Produto,
    ProdutoFilial,
    TabelaPreco,
    TabelaPrecoFilial,
    UnidadeMedida,
    UnidadeMedidaFilial,
)
from apps.vendas.models import PedidoVenda
from apps.vendas.services.venda_service import VendaService


class ForcaVendasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Distribuidora Teste LTDA',
            nome_fantasia='Distribuidora Teste',
            cnpj='82345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Distribuidora Teste Matriz',
            nome_fantasia='Matriz',
            cnpj='82345678000192',
            uf='RN',
            cidade='Natal',
            is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Gestor Comercial',
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='gestor-forca-vendas@inoovated.com',
            nome='Gestor Comercial',
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
        cls.tabela = TabelaPreco.objects.create(
            filial=cls.filial,
            descricao='Atacado externo',
            tipo=TabelaPreco.Tipo.ATACADO,
        )
        TabelaPrecoFilial.objects.create(tabela=cls.tabela, filial=cls.filial)
        cls.cliente = Cliente.objects.create(
            filial=cls.filial,
            tipo_pessoa='J',
            razao_social='Mercadinho Cliente LTDA',
            nome_fantasia='Mercadinho Cliente',
            cpf_cnpj='82345678000193',
            cidade='Natal',
            uf='RN',
            tabela_preco=cls.tabela,
            limite_credito=Decimal('1000.00'),
            ativo=True,
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.produto = Produto.objects.create(
            filial=cls.filial,
            unidade_medida=cls.unidade,
            descricao='Caixa de Polpa',
            codigo='POLPA-001',
            ncm='20089900',
            controla_lote=False,
            permite_venda_sem_estoque=False,
            preco_venda=Decimal('10.00'),
            preco_custo=Decimal('4.00'),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)
        ItemTabelaPreco.objects.create(
            tabela=cls.tabela,
            produto=cls.produto,
            preco_unitario=Decimal('7.50'),
            quantidade_minima=0,
        )
        ItemTabelaPreco.objects.create(
            tabela=cls.tabela,
            produto=cls.produto,
            preco_unitario=Decimal('6.50'),
            quantidade_minima=5,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

    def criar_pedido_pela_tela(self, quantidade='2'):
        return self.client.post(
            reverse('vendas:pedido-externo-novo'),
            data=json.dumps({
                'cliente_id': self.cliente.pk,
                'itens': [{
                    'produto_id': self.produto.pk,
                    'quantidade': quantidade,
                    # O servidor deve ignorar qualquer preço enviado pelo navegador.
                    'valor_unitario': '0.01',
                }],
                'prioridade': 'urgente',
                'observacao': 'Entregar pela manhã.',
            }),
            content_type='application/json',
        )

    def abastecer(self, quantidade='20'):
        MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(quantidade),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('4.00'),
        )

    def test_modulo_e_menu_reunem_as_tres_telas(self):
        self.assertEqual(modulo_da_url('/vendas/acompanhamento/'), 'forca_vendas')
        self.assertIn('forca_vendas', modulos_ativos(self.filial))

        resposta = self.client.get(reverse('vendas:pedido-externo-novo'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Força de Vendas')
        self.assertContains(resposta, reverse('vendas:pedido-externo-novo'))
        self.assertContains(resposta, reverse('vendas:pedido-kanban'))
        self.assertContains(resposta, reverse('vendas:pedido-separacao'))
        self.assertNotContains(resposta, 'Pré-venda')

    def test_busca_produto_exibe_preco_da_tabela_do_cliente(self):
        resposta = self.client.get(reverse('vendas:api-produtos'), {
            'cliente_id': self.cliente.pk,
            'q': 'Polpa',
        })

        self.assertEqual(resposta.status_code, 200)
        produto = resposta.json()['produtos'][0]
        self.assertEqual(Decimal(str(produto['preco'])), Decimal('7.5'))
        self.assertIn('Atacado externo', produto['preco_origem'])

        resposta_volume = self.client.get(reverse('vendas:api-produtos'), {
            'cliente_id': self.cliente.pk,
            'produto_id': self.produto.pk,
            'quantidade': '6',
        })
        self.assertEqual(
            Decimal(str(resposta_volume.json()['produtos'][0]['preco'])),
            Decimal('6.5'),
        )

    def test_pedido_externo_usa_preco_do_servidor_e_nao_cai_no_pdv(self):
        resposta = self.criar_pedido_pela_tela()

        self.assertEqual(resposta.status_code, 200, resposta.content)
        pedido = PedidoVenda.objects.get(pk=resposta.json()['pedido_id'])
        item = pedido.itens.get()
        self.assertEqual(pedido.origem, PedidoVenda.Origem.EXTERNO)
        self.assertEqual(pedido.status, PedidoVenda.Status.AGUARDANDO_APROVACAO)
        self.assertEqual(pedido.prioridade, PedidoVenda.Prioridade.URGENTE)
        self.assertEqual(item.valor_unitario, Decimal('7.5000'))
        self.assertEqual(pedido.valor_total, Decimal('15.00'))
        self.assertEqual(VendaPDV.objects.count(), 0)

    def test_kanban_nao_mistura_pedidos_internos(self):
        interno = VendaService.criar_pedido(
            self.filial,
            self.usuario,
            self.cliente,
        )
        externo_id = self.criar_pedido_pela_tela().json()['pedido_id']
        externo = PedidoVenda.objects.get(pk=externo_id)

        resposta = self.client.get(reverse('vendas:pedido-kanban'))

        self.assertContains(resposta, externo.numero_pedido)
        self.assertNotContains(resposta, interno.numero_pedido)

    def test_kanban_aprovacao_e_fila_de_separacao_fecham_o_fluxo(self):
        self.abastecer()
        criado = self.criar_pedido_pela_tela().json()
        pedido = PedidoVenda.objects.get(pk=criado['pedido_id'])

        kanban = self.client.get(reverse('vendas:pedido-kanban'))
        self.assertContains(kanban, pedido.numero_pedido)
        self.assertContains(kanban, 'Analisar e liberar')

        aprovacao = self.client.post(
            reverse('vendas:pedido-aprovar-externo', args=[pedido.pk]),
        )
        self.assertRedirects(aprovacao, reverse('vendas:pedido-kanban'))
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, PedidoVenda.Status.CONFIRMADO)
        estoque = Estoque.objects.get(produto=self.produto, filial=self.filial)
        self.assertEqual(estoque.quantidade_reservada, Decimal('2.000'))

        fila = self.client.get(reverse('vendas:pedido-separacao'))
        self.assertContains(fila, pedido.numero_pedido)
        self.assertContains(fila, 'Confirmar separação')

        separacao = self.client.post(
            reverse('vendas:pedido-separar', args=[pedido.pk]),
            {'origem': 'fila-separacao'},
        )
        self.assertRedirects(separacao, reverse('vendas:pedido-separacao'))
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, PedidoVenda.Status.EM_SEPARACAO)
        self.assertTrue(pedido.separacoes.exists())

        fila_atualizada = self.client.get(reverse('vendas:pedido-separacao'))
        self.assertContains(fila_atualizada, 'Aguardando faturamento')

    def test_payload_malformado_retorna_erro_json(self):
        resposta = self.client.post(
            reverse('vendas:pedido-externo-novo'),
            data=json.dumps([]),
            content_type='application/json',
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertIn('inválidos', resposta.json()['erro'])
