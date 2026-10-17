from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import CondicaoPagamento, FormaPagamento
from apps.produtos.models import (
    Produto,
    ProdutoFilial,
    UnidadeMedida,
    UnidadeMedidaFilial,
)
from apps.vendas.models import ItemSeparacao, PedidoVenda, SeparacaoPedido
from apps.vendas.services.venda_service import VendaService


class ForcaVendasMobileApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='ITED Mobile Teste LTDA',
            nome_fantasia='ITED Mobile',
            cnpj='71345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='ITED Mobile Matriz',
            nome_fantasia='Matriz Mobile',
            cnpj='71345678000192',
            uf='RN',
            cidade='Natal',
            is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Administrador Mobile',
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='mobile@ited.test',
            nome='Usuário Mobile',
            password='senha-mobile-123',
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
        cls.produto = Produto.objects.create(
            filial=cls.filial,
            unidade_medida=cls.unidade,
            descricao='Produto para pedido mobile',
            codigo='MOB-001',
            ncm='20089900',
            controla_lote=False,
            permite_venda_sem_estoque=False,
            preco_venda=Decimal('12.50'),
            preco_custo=Decimal('5.00'),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)
        cls.cliente = Cliente.objects.create(
            filial=cls.filial,
            tipo_pessoa='J',
            razao_social='Cliente Mobile LTDA',
            nome_fantasia='Cliente Mobile',
            cpf_cnpj='71345678000193',
            cidade='Natal',
            uf='RN',
            limite_credito=Decimal('1000.00'),
            ativo=True,
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.forma = FormaPagamento.objects.create(
            empresa=cls.empresa,
            filial=cls.filial,
            descricao='Boleto mobile',
            tipo=TipoFormaPagamento.BOLETO,
        )
        cls.condicao = CondicaoPagamento.objects.create(
            empresa=cls.empresa,
            descricao='À vista mobile',
            numero_parcelas=1,
        )

    def setUp(self):
        token = self.client.post(
            reverse('forca_vendas_mobile_api:token'),
            data={'email': self.usuario.email, 'password': 'senha-mobile-123'},
            content_type='application/json',
        )
        self.assertEqual(token.status_code, 200, token.content)
        self.headers = {
            'HTTP_AUTHORIZATION': f'Bearer {token.json()["access"]}',
            'HTTP_X_FILIAL_ID': str(self.filial.pk),
            'HTTP_X_TENANT_ALIAS': 'default',
        }

    def _payload_pedido(self):
        return {
            'cliente_id': self.cliente.pk,
            'forma_pagamento_id': self.forma.pk,
            'condicao_pagamento_id': self.condicao.pk,
            'prazo_pagamento_dias': 10,
            'prioridade': 'urgente',
            'observacao': 'Criado no Android.',
            'itens': [{
                'produto_id': self.produto.pk,
                'quantidade': '2.000',
            }],
        }

    def test_login_contexto_e_catalogos_do_pedido(self):
        contexto = self.client.get(
            reverse('forca_vendas_mobile_api:contexto'),
            HTTP_AUTHORIZATION=self.headers['HTTP_AUTHORIZATION'],
        )
        self.assertEqual(contexto.status_code, 200, contexto.content)
        self.assertEqual(contexto.json()['contextos'][0]['filial_id'], self.filial.pk)

        clientes = self.client.get(
            reverse('forca_vendas_mobile_api:clientes'),
            {'q': str(self.cliente.pk)},
            **self.headers,
        )
        self.assertEqual(clientes.status_code, 200, clientes.content)
        self.assertEqual(clientes.json()['clientes'][0]['nome'], 'Cliente Mobile')

        produtos = self.client.get(
            reverse('forca_vendas_mobile_api:produtos'),
            {'cliente_id': self.cliente.pk, 'q': 'MOB-001'},
            **self.headers,
        )
        self.assertEqual(produtos.status_code, 200, produtos.content)
        self.assertEqual(produtos.json()['produtos'][0]['preco'], '12.50')

        opcoes = self.client.get(
            reverse('forca_vendas_mobile_api:opcoes_pedido'),
            **self.headers,
        )
        self.assertEqual(opcoes.status_code, 200, opcoes.content)
        self.assertIn(
            self.forma.pk,
            [forma['id'] for forma in opcoes.json()['formas_pagamento']],
        )

    def test_pedido_mobile_e_idempotente_e_recebe_numero_oficial(self):
        chave = 'pedido-mobile-0001'
        url = reverse('forca_vendas_mobile_api:pedidos')
        primeira = self.client.post(
            url,
            data=self._payload_pedido(),
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY=chave,
            **self.headers,
        )
        self.assertEqual(primeira.status_code, 201, primeira.content)
        self.assertFalse(primeira.json()['repetido'])
        self.assertTrue(primeira.json()['pedido']['numero_pedido'].startswith('PV-'))

        repetida = self.client.post(
            url,
            data=self._payload_pedido(),
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY=chave,
            **self.headers,
        )
        self.assertEqual(repetida.status_code, 200, repetida.content)
        self.assertTrue(repetida.json()['repetido'])
        self.assertEqual(PedidoVenda.objects.count(), 1)

    def test_fila_e_conclusao_da_separacao_mobile_sao_idempotentes(self):
        MovimentacaoService.registrar_movimentacao(
            produto_id=self.produto.pk,
            filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal('10'),
            usuario_id=self.usuario.pk,
            valor_unitario=Decimal('5'),
        )
        criado = self.client.post(
            reverse('forca_vendas_mobile_api:pedidos'),
            data=self._payload_pedido(),
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='pedido-mobile-separar-1',
            **self.headers,
        )
        pedido = PedidoVenda.objects.get(pk=criado.json()['pedido']['id'])
        VendaService.aprovar_e_confirmar_pedido(pedido, self.usuario)

        fila = self.client.get(
            reverse('forca_vendas_mobile_api:separacoes'),
            **self.headers,
        )
        self.assertEqual(fila.status_code, 200, fila.content)
        item = fila.json()['pedidos'][0]['itens'][0]
        deposito_id = Deposito.padrao_id(self.filial.pk)
        payload = {
            'observacao': 'Conferido pelo Android.',
            'itens': [{
                'item_id': item['id'],
                'deposito_id': deposito_id,
                'quantidade_separada': '2.000',
                'conferido': True,
            }],
        }
        url = reverse('forca_vendas_mobile_api:concluir_separacao', args=[pedido.pk])
        primeira = self.client.post(
            url,
            data=payload,
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='separacao-mobile-0001',
            **self.headers,
        )
        self.assertEqual(primeira.status_code, 201, primeira.content)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, PedidoVenda.Status.EM_SEPARACAO)

        repetida = self.client.post(
            url,
            data=payload,
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='separacao-mobile-0001',
            **self.headers,
        )
        self.assertEqual(repetida.status_code, 200, repetida.content)
        self.assertTrue(repetida.json()['repetido'])
        self.assertEqual(SeparacaoPedido.objects.count(), 1)

    def test_separacao_mobile_permite_dividir_quantidade_entre_lotes(self):
        self.produto.controla_lote = True
        self.produto.save(update_fields=['controla_lote', 'updated_at'])
        lote = LoteProduto.objects.create(
            produto=self.produto,
            filial=self.filial,
            numero_lote='LOTE-MOBILE-02',
            quantidade_inicial=Decimal('0'),
            quantidade_atual=Decimal('0'),
            custo_unitario=Decimal('5'),
        )
        outro_lote = LoteProduto.objects.create(
            produto=self.produto,
            filial=self.filial,
            numero_lote='LOTE-MOBILE-03',
            quantidade_inicial=Decimal('0'),
            quantidade_atual=Decimal('0'),
            custo_unitario=Decimal('5'),
        )
        for lote_entrada in [lote, outro_lote]:
            MovimentacaoService.registrar_movimentacao(
                produto_id=self.produto.pk,
                filial_id=self.filial.pk,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
                quantidade=Decimal('10'),
                usuario_id=self.usuario.pk,
                lote_id=lote_entrada.pk,
                valor_unitario=Decimal('5'),
            )
        criado = self.client.post(
            reverse('forca_vendas_mobile_api:pedidos'),
            data=self._payload_pedido(),
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='pedido-mobile-lote-001',
            **self.headers,
        )
        self.assertEqual(criado.status_code, 201, criado.content)
        pedido = PedidoVenda.objects.get(pk=criado.json()['pedido']['id'])
        VendaService.aprovar_e_confirmar_pedido(pedido, self.usuario)

        fila = self.client.get(
            reverse('forca_vendas_mobile_api:separacoes'),
            **self.headers,
        )
        item = fila.json()['pedidos'][0]['itens'][0]
        self.assertEqual(item['lote_modo'], 'manual_ou_fefo')
        resposta = self.client.post(
            reverse('forca_vendas_mobile_api:concluir_separacao', args=[pedido.pk]),
            data={
                'itens': [{
                    'item_id': item['id'],
                    'deposito_id': Deposito.padrao_id(self.filial.pk),
                    'quantidade_separada': '2.000',
                    'conferido': True,
                    'lotes': [
                        {'lote_id': lote.pk, 'quantidade': '1.000'},
                        {'lote_id': outro_lote.pk, 'quantidade': '1.000'},
                    ],
                }],
            },
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='separacao-mobile-lote-001',
            **self.headers,
        )
        self.assertEqual(resposta.status_code, 201, resposta.content)
        itens_separados = ItemSeparacao.objects.filter(
            separacao_id=resposta.json()['separacao_id'],
        ).order_by('lote_id')
        self.assertEqual(itens_separados.count(), 2)
        self.assertEqual(
            {item.lote_id: item.quantidade_separada for item in itens_separados},
            {lote.pk: Decimal('1.000'), outro_lote.pk: Decimal('1.000')},
        )
