import json
from datetime import datetime, time
from decimal import Decimal
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from django.contrib.auth.models import AnonymousUser
from django.template.loader import get_template
from django.urls import resolve, reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.cadastros.models import Cliente
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial
from apps.whatsapp_agent.agent import processar_mensagem
from apps.whatsapp_agent.models import ConfiguracaoWhatsApp, ConversaWhatsApp

from apps.catalogo.models import (
    CatalogoConfiguracao, CatalogoLinkPublico, CupomCatalogo,
    ItemPedidoCatalogo, PedidoCatalogo,
)
from apps.catalogo.services import formatar_resumo
from apps.catalogo.views import CatalogoPainelView, PedidoAcaoView, PedidosCatalogoView
from apps.catalogo.views_publico import (
    CatalogoPublicoView, ClienteCatalogoView, CupomCatalogoPublicoView,
    PedidoCatalogoConfirmarView, _funcionamento,
)
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.pdv.views.pdv import _pedido_catalogo_para_checkout


class CatalogoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Mercado Catálogo LTDA', nome_fantasia='Mercado Catálogo',
            cnpj='42345678000191', regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Mercado Catálogo', nome_fantasia='Mercado',
            cnpj='42345678000192', uf='CE',
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Administrador catálogo', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='catalogo@teste.local', nome='Usuário Catálogo', password='teste1234',
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade, descricao='Café 500g',
            ncm='09012100', preco_venda=Decimal('20.00'), exibir_catalogo=True,
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)
        cls.link = CatalogoLinkPublico.objects.create(filial=cls.filial, token='catalogo-teste')

    def setUp(self):
        self.factory = RequestFactory()

    def test_frete_gratis_acima_do_limite_e_fixo_abaixo(self):
        config = CatalogoConfiguracao.objects.create(
            filial=self.filial, frete_gratis_ativo=True,
            valor_minimo_frete_gratis=Decimal('100.00'),
            frete_abaixo_limite=CatalogoConfiguracao.FreteAbaixoLimite.FIXO,
            valor_frete=Decimal('8.00'),
        )
        self.assertEqual(config.calcular_frete(Decimal('99.99'), 'entrega'), (Decimal('8.00'), False))
        self.assertEqual(config.calcular_frete(Decimal('100.00'), 'entrega'), (0, False))
        self.assertEqual(config.calcular_frete(Decimal('10.00'), 'retirada'), (0, False))

    def test_frete_a_combinar_nao_entra_no_total(self):
        config = CatalogoConfiguracao.objects.create(
            filial=self.filial,
            frete_abaixo_limite=CatalogoConfiguracao.FreteAbaixoLimite.A_COMBINAR,
        )
        self.assertEqual(config.calcular_frete(Decimal('50.00'), 'entrega'), (0, True))

    def test_resposta_um_confirma_pedido_para_a_loja(self):
        cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='F', razao_social='Cliente WhatsApp',
            celular='5584999990000',
        )
        config = ConfiguracaoWhatsApp.objects.create(
            filial=self.filial, instancia='catalogo-whatsapp-teste', agente_ativo=True,
        )
        conversa = ConversaWhatsApp.objects.create(
            filial=self.filial, configuracao=config,
            remote_jid='5584999990000@s.whatsapp.net', telefone='5584999990000',
            etapa='aguardando_confirmacao_pedido',
        )
        pedido = PedidoCatalogo.objects.create(
            filial=self.filial, numero='CAT-TESTE', cliente=cliente,
            nome_cliente=cliente.nome_display, telefone=cliente.celular,
            modalidade='retirada', forma_pagamento='pix',
            subtotal=Decimal('20.00'), total=Decimal('20.00'),
        )
        conversa.contexto = {'catalogo_pedido_id': pedido.pk, 'catalogo_db_alias': 'default'}
        conversa.save(update_fields=['contexto', 'updated_at'])

        resposta = processar_mensagem(conversa, '1')

        pedido.refresh_from_db()
        self.assertEqual(pedido.status, PedidoCatalogo.Status.AGUARDANDO_LOJA)
        self.assertIn('confirmado', resposta.lower())

    def test_catalogo_publico_exibe_produto_habilitado(self):
        self.filial.endereco = 'Rua do Catálogo'
        self.filial.numero = '120'
        self.filial.bairro = 'Centro'
        self.filial.cidade = 'Fortaleza'
        self.filial.save(update_fields=['endereco', 'numero', 'bairro', 'cidade', 'updated_at'])
        self.produto.catalogo_destaque = True
        self.produto.catalogo_descricao = 'Café torrado e moído.'
        self.produto.save(update_fields=['catalogo_destaque', 'catalogo_descricao', 'updated_at'])
        request = self.factory.get('/pedir/catalogo-teste/')
        request.user = AnonymousUser()
        response = CatalogoPublicoView.as_view()(request, token=self.link.token)
        self.assertEqual(response.status_code, 200)
        conteudo = response.content.decode()
        self.assertIn('Café 500g', conteudo)
        self.assertIn('data-remove=', conteudo)
        self.assertIn('data-quantity=', conteudo)
        self.assertIn('Rua do Catálogo, 120', conteudo)
        self.assertIn('Destaques', conteudo)
        self.assertIn('Café torrado e moído.', conteudo)
        self.assertIn('data-category-filter=', conteudo)
        self.assertIn('Peça também', conteudo)
        self.assertIn('Cupom de desconto', conteudo)

    def test_status_de_funcionamento_indica_aberto_e_fechado(self):
        config = CatalogoConfiguracao.objects.create(
            filial=self.filial, dias_funcionamento=[3],
            horario_abertura=time(8), horario_fechamento=time(18),
        )
        aberto = _funcionamento(
            config, timezone.make_aware(datetime(2026, 10, 8, 10, 0)),
        )
        fechado = _funcionamento(
            config, timezone.make_aware(datetime(2026, 10, 8, 20, 0)),
        )

        self.assertTrue(aberto['aberto'])
        self.assertEqual(aberto['status'], 'Aberto agora · até 18:00')
        self.assertFalse(fechado['aberto'])
        self.assertEqual(fechado['status'], 'Fechado agora')

    def test_cupom_publico_valida_e_calcula_desconto(self):
        CupomCatalogo.objects.create(
            filial=self.filial, codigo='BEMVINDO',
            tipo=CupomCatalogo.Tipo.PERCENTUAL, valor=Decimal('10.00'),
            pedido_minimo=Decimal('30.00'),
        )
        request = self.factory.post(
            '/pedir/catalogo-teste/cupom/',
            data=json.dumps({'codigo': 'bemvindo', 'subtotal': 50}),
            content_type='application/json',
        )

        response = CupomCatalogoPublicoView.as_view()(request, token=self.link.token)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['codigo'], 'BEMVINDO')
        self.assertEqual(payload['desconto'], 5.0)

    @patch('apps.catalogo.views_publico.enviar_resumo_whatsapp', return_value=(True, 'Enviado'))
    def test_checkout_aplica_cupom_e_grava_total_com_desconto(self, _enviar):
        CatalogoConfiguracao.objects.create(filial=self.filial)
        cupom = CupomCatalogo.objects.create(
            filial=self.filial, codigo='MENOS5', tipo=CupomCatalogo.Tipo.VALOR,
            valor=Decimal('5.00'),
        )
        request = self.factory.post('/pedir/catalogo-teste/', {
            'nome': 'Cliente com Cupom', 'telefone': '84999997777',
            'modalidade': 'retirada', 'forma_pagamento': 'pix',
            'cupom_codigo': 'menos5',
            'carrinho_json': json.dumps([{'id': self.produto.pk, 'quantidade': 2}]),
        })
        request.user = AnonymousUser()

        with patch('apps.catalogo.views_publico.conversa_do_token', return_value=object()):
            response = CatalogoPublicoView.as_view()(request, token=self.link.token)

        pedido = PedidoCatalogo.objects.get(cliente__razao_social='Cliente com Cupom')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pedido.cupom, cupom)
        self.assertEqual(pedido.codigo_cupom, 'MENOS5')
        self.assertEqual(pedido.subtotal, Decimal('40.00'))
        self.assertEqual(pedido.valor_desconto, Decimal('5.00'))
        self.assertEqual(pedido.total, Decimal('35.00'))
        self.assertEqual(pedido.status, PedidoCatalogo.Status.AGUARDANDO_CLIENTE)
        self.assertContains(response, 'Revise seu pedido')
        self.assertContains(response, 'Confirmar pedido')
        self.assertContains(response, 'Voltar e editar')
        _enviar.assert_not_called()

    @patch('apps.catalogo.views_publico.enviar_resumo_whatsapp', return_value=(True, 'Resumo enviado.'))
    def test_cliente_confirma_no_catalogo_e_so_entao_recebe_resumo(self, enviar):
        cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='F', razao_social='Cliente Confirmação',
            celular='5584999997888',
        )
        pedido = PedidoCatalogo.objects.create(
            filial=self.filial, numero='CAT-CONFIRMA', cliente=cliente,
            nome_cliente=cliente.nome_display, telefone=cliente.celular,
            modalidade='retirada', forma_pagamento='pix',
            subtotal=Decimal('20.00'), total=Decimal('20.00'),
        )
        ItemPedidoCatalogo.objects.create(
            pedido=pedido, produto=self.produto, descricao=self.produto.descricao,
            quantidade=1, valor_unitario=Decimal('20.00'), valor_total=Decimal('20.00'),
        )
        request = self.factory.post(
            f'/pedir/{self.link.token}/pedido/{pedido.token}/confirmar/',
        )
        request.user = AnonymousUser()

        response = PedidoCatalogoConfirmarView.as_view()(
            request, token=self.link.token, pedido_token=pedido.token,
        )

        pedido.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pedido.status, PedidoCatalogo.Status.AGUARDANDO_LOJA)
        self.assertIsNotNone(pedido.confirmado_cliente_em)
        self.assertContains(response, 'Pedido confirmado!')
        enviar.assert_called_once()

    def test_resumo_do_whatsapp_nao_pede_confirmacao_por_numero(self):
        cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='F', razao_social='Cliente Resumo',
            celular='5584999997666',
        )
        pedido = PedidoCatalogo.objects.create(
            filial=self.filial, numero='CAT-RESUMO', cliente=cliente,
            nome_cliente=cliente.nome_display, telefone=cliente.celular,
            modalidade='retirada', forma_pagamento='pix',
            subtotal=Decimal('20.00'), total=Decimal('20.00'),
        )
        ItemPedidoCatalogo.objects.create(
            pedido=pedido, produto=self.produto, descricao=self.produto.descricao,
            quantidade=1, valor_unitario=Decimal('20.00'), valor_total=Decimal('20.00'),
        )
        configuracao = ConfiguracaoWhatsApp.objects.create(
            filial=self.filial, instancia='catalogo-resumo-teste',
        )

        texto = formatar_resumo(pedido, configuracao)

        self.assertIn('Pedido confirmado', texto)
        self.assertNotIn('Confirmar pedido', texto)
        self.assertNotIn('Refazer pedido', texto)

    def test_voltar_para_editar_reabre_itens_do_pedido(self):
        cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='F', razao_social='Cliente Edição',
            celular='5584999997999',
        )
        pedido = PedidoCatalogo.objects.create(
            filial=self.filial, numero='CAT-EDITA', cliente=cliente,
            nome_cliente=cliente.nome_display, telefone=cliente.celular,
            modalidade='retirada', forma_pagamento='pix',
            subtotal=Decimal('40.00'), total=Decimal('40.00'),
        )
        ItemPedidoCatalogo.objects.create(
            pedido=pedido, produto=self.produto, descricao=self.produto.descricao,
            quantidade=2, valor_unitario=Decimal('20.00'), valor_total=Decimal('40.00'),
        )
        request = self.factory.get(
            f'/pedir/{self.link.token}/', {'editar': pedido.token},
        )
        request.user = AnonymousUser()

        response = CatalogoPublicoView.as_view()(request, token=self.link.token)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{pedido.token}"')
        self.assertContains(response, '"quantidade": 2')

    def test_configuracao_e_pedidos_ficam_em_telas_separadas(self):
        request_config = self.factory.get('/catalogo/')
        request_config.filial_ativa = self.filial
        request_config.user = AnonymousUser()
        request_pedidos = self.factory.get('/catalogo/pedidos/')
        request_pedidos.filial_ativa = self.filial
        request_pedidos.user = AnonymousUser()

        config_response = CatalogoPainelView().get(request_config)
        pedidos_response = PedidosCatalogoView().get(request_pedidos)

        self.assertEqual(config_response.status_code, 200)
        self.assertEqual(pedidos_response.status_code, 200)
        self.assertIsNotNone(get_template('catalogo/painel.html'))
        self.assertIsNotNone(get_template('catalogo/pedidos.html'))

    def test_pedido_separado_vai_para_pendencia_do_caixa(self):
        cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='F', razao_social='Cliente Caixa',
            celular='5584999991111',
        )
        pedido = PedidoCatalogo.objects.create(
            filial=self.filial, numero='CAT-CAIXA', cliente=cliente,
            nome_cliente=cliente.nome_display, telefone=cliente.celular,
            modalidade='retirada', forma_pagamento='pix',
            subtotal=Decimal('20.00'), total=Decimal('20.00'),
            status=PedidoCatalogo.Status.EM_SEPARACAO,
        )
        request = self.factory.post(f'/catalogo/pedidos/{pedido.pk}/pendencia/')
        request.filial_ativa = self.filial
        request.user = AnonymousUser()

        with patch('apps.catalogo.views.messages.success'), patch(
            'apps.catalogo.views.enviar_atualizacao_whatsapp', return_value=True,
        ):
            response = PedidoAcaoView().post(request, pedido.pk, 'pendencia')

        pedido.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(pedido.status, PedidoCatalogo.Status.PENDENTE_CAIXA)
        self.assertEqual(response.url, '/catalogo/pedidos/')

        painel_request = self.factory.get('/catalogo/pedidos/')
        painel_request.filial_ativa = self.filial
        painel_request.user = self.usuario
        painel_request.resolver_match = resolve('/catalogo/pedidos/')
        painel = PedidosCatalogoView().get(painel_request)
        conteudo = painel.content.decode()
        trecho_pedidos = conteudo[conteudo.find('<div class="orders-page">'):]
        self.assertIn(f'pedido_catalogo={pedido.pk}', trecho_pedidos)
        self.assertIn('Abrir no PDV', trecho_pedidos)
        self.assertIn(reverse('catalogo:pedido-imprimir', args=[pedido.pk]), trecho_pedidos)

    def test_folha_de_separacao_abre_pronta_para_imprimir(self):
        cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='F', razao_social='Cliente Impressão',
            celular='5584999998111',
        )
        pedido = PedidoCatalogo.objects.create(
            filial=self.filial, numero='CAT-IMPRESSAO', cliente=cliente,
            nome_cliente=cliente.nome_display, telefone=cliente.celular,
            modalidade='entrega', forma_pagamento='pix',
            endereco_entrega={
                'logradouro': 'Rua da Separação', 'numero': '42',
                'bairro': 'Centro', 'cidade': 'Fortaleza', 'uf': 'CE',
            },
            subtotal=Decimal('40.00'), total=Decimal('40.00'),
            status=PedidoCatalogo.Status.APROVADO,
            observacao='Embalar separado.',
        )
        ItemPedidoCatalogo.objects.create(
            pedido=pedido, produto=self.produto, descricao=self.produto.descricao,
            quantidade=2, valor_unitario=Decimal('20.00'), valor_total=Decimal('40.00'),
        )
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

        resposta = self.client.get(reverse('catalogo:pedido-imprimir', args=[pedido.pk]))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Folha de separação')
        self.assertContains(resposta, 'CAT-IMPRESSAO')
        self.assertContains(resposta, 'Rua da Separação')
        self.assertContains(resposta, 'Embalar separado.')
        self.assertContains(resposta, 'window.print()')
        self.assertEqual(resposta['Cache-Control'], 'private, no-store')

    def test_checkout_do_catalogo_carrega_cliente_itens_e_frete(self):
        cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='F', razao_social='Cliente Checkout',
            celular='5584999992222',
        )
        pedido = PedidoCatalogo.objects.create(
            filial=self.filial, numero='CAT-CHECKOUT', cliente=cliente,
            nome_cliente=cliente.nome_display, telefone=cliente.celular,
            modalidade='entrega', forma_pagamento='pix', endereco_entrega={'rua': 'Rua A'},
            subtotal=Decimal('40.00'), valor_frete=Decimal('8.00'), total=Decimal('48.00'),
            status=PedidoCatalogo.Status.PENDENTE_CAIXA,
        )
        ItemPedidoCatalogo.objects.create(
            pedido=pedido, produto=self.produto, descricao=self.produto.descricao,
            quantidade=2, valor_unitario=Decimal('20.00'), valor_total=Decimal('40.00'),
        )
        request = self.factory.get('/pdv/checkout/', {'pedido_catalogo': pedido.pk})
        request.filial_ativa = self.filial

        checkout = _pedido_catalogo_para_checkout(request)

        self.assertEqual(checkout['id'], pedido.pk)
        self.assertEqual(checkout['cliente']['id'], cliente.pk)
        self.assertEqual(checkout['itens'][0]['quantidade'], 2)
        self.assertEqual(checkout['itens'][0]['produto_id'], self.produto.pk)
        self.assertEqual(checkout['frete'], 8.0)
        self.assertEqual(checkout['desconto'], 0.0)
        self.assertTrue(checkout['delivery'])

    def test_cliente_localizado_recebe_historico_de_compras(self):
        cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='F', razao_social='Cliente Recorrente',
            celular='5584999993333',
        )
        pedido = PedidoCatalogo.objects.create(
            filial=self.filial, numero='CAT-HISTORICO', cliente=cliente,
            nome_cliente=cliente.nome_display, telefone=cliente.celular,
            modalidade='retirada', forma_pagamento='pix',
            subtotal=Decimal('20.00'), total=Decimal('20.00'),
            status=PedidoCatalogo.Status.PAGO,
        )
        ItemPedidoCatalogo.objects.create(
            pedido=pedido, produto=self.produto, descricao=self.produto.descricao,
            quantidade=1, valor_unitario=Decimal('20.00'), valor_total=Decimal('20.00'),
        )
        request = self.factory.get(
            '/pedir/catalogo-teste/cliente/', {'telefone': '84999993333'},
        )

        response = ClienteCatalogoView.as_view()(request, token=self.link.token)
        payload = json.loads(response.content)

        self.assertTrue(payload['encontrado'])
        self.assertEqual(payload['nome'], 'Cliente Recorrente')
        self.assertEqual(payload['historico'][0]['numero'], 'CAT-HISTORICO')
        self.assertEqual(payload['historico'][0]['itens'][0]['id'], self.produto.pk)
        self.assertEqual(payload['historico'][0]['itens'][0]['nome'], 'Café 500g')

    def test_historico_do_catalogo_inclui_vendas_finalizadas_no_pdv(self):
        cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='F', razao_social='Cliente do PDV',
            celular='5584999993555',
        )
        venda = VendaPDV.objects.create(
            filial=self.filial, numero_venda=27, cliente=cliente,
            usuario=self.usuario, data_venda=timezone.now(), status='finalizada',
            valor_subtotal=Decimal('40.00'), valor_total=Decimal('40.00'),
            valor_pago=Decimal('40.00'),
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=self.produto, numero_item=1,
            unidade_medida='UN', quantidade=Decimal('2'),
            valor_unitario=Decimal('20.00'), valor_total=Decimal('40.00'),
        )
        request = self.factory.get(
            '/pedir/catalogo-teste/cliente/', {'telefone': '(84) 99999-3555'},
        )

        response = ClienteCatalogoView.as_view()(request, token=self.link.token)
        payload = json.loads(response.content)

        self.assertTrue(payload['encontrado'])
        self.assertEqual(payload['historico'][0]['numero'], 'Venda #000027')
        self.assertEqual(payload['historico'][0]['itens'], [
            {'id': self.produto.pk, 'nome': 'Café 500g', 'quantidade': 2.0},
        ])

    @patch('apps.catalogo.views_publico.enviar_resumo_whatsapp', return_value=(True, 'Enviado'))
    def test_repetir_compra_cria_novo_pedido_com_preco_atual(self, _enviar):
        cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='F', razao_social='Cliente Recorrente',
            celular='5584999993666',
        )
        venda = VendaPDV.objects.create(
            filial=self.filial, numero_venda=28, cliente=cliente,
            usuario=self.usuario, data_venda=timezone.now(), status='finalizada',
            valor_subtotal=Decimal('20.00'), valor_total=Decimal('20.00'),
            valor_pago=Decimal('20.00'),
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=self.produto, numero_item=1,
            unidade_medida='UN', quantidade=Decimal('1'),
            valor_unitario=Decimal('20.00'), valor_total=Decimal('20.00'),
        )
        self.produto.preco_venda = Decimal('25.00')
        self.produto.save(update_fields=['preco_venda'])
        request = self.factory.post('/pedir/catalogo-teste/', {
            'nome': cliente.nome_display, 'telefone': cliente.celular,
            'modalidade': 'retirada', 'forma_pagamento': 'pix',
            'carrinho_json': json.dumps([{'id': self.produto.pk, 'quantidade': 2}]),
        })
        request.user = AnonymousUser()

        with patch('apps.catalogo.views_publico.conversa_do_token', return_value=object()):
            response = CatalogoPublicoView.as_view()(request, token=self.link.token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(VendaPDV.objects.filter(cliente=cliente).count(), 1)
        pedido = PedidoCatalogo.objects.get(cliente=cliente)
        self.assertIsNone(pedido.venda_pdv_id)
        self.assertEqual(pedido.subtotal, Decimal('50.00'))
        self.assertEqual(pedido.total, Decimal('50.00'))
        self.assertEqual(pedido.itens.get().valor_unitario, Decimal('25.00'))

    @patch('apps.catalogo.views_publico.enviar_resumo_whatsapp', return_value=(True, 'Enviado'))
    def test_checkout_recalcula_valores_e_cria_cliente(self, _enviar):
        CatalogoConfiguracao.objects.create(
            filial=self.filial,
            frete_abaixo_limite=CatalogoConfiguracao.FreteAbaixoLimite.FIXO,
            valor_frete=Decimal('7.00'),
        )
        request = self.factory.post('/pedir/catalogo-teste/', {
            'nome': 'Cliente Catálogo', 'telefone': '84999990000',
            'modalidade': 'entrega', 'forma_pagamento': 'pix',
            'logradouro': 'Rua Teste', 'numero': '10', 'bairro': 'Centro',
            'cidade': 'Fortaleza', 'uf': 'CE', 'cep': '60000000',
            'carrinho_json': json.dumps([{'id': self.produto.pk, 'quantidade': 2}]),
        })
        request.user = AnonymousUser()
        with patch('apps.catalogo.views_publico.conversa_do_token', return_value=object()):
            response = CatalogoPublicoView.as_view()(request, token=self.link.token)
        self.assertEqual(response.status_code, 200)
        pedido = PedidoCatalogo.objects.get()
        self.assertEqual(pedido.subtotal, Decimal('40.00'))
        self.assertEqual(pedido.valor_frete, Decimal('7.00'))
        self.assertEqual(pedido.total, Decimal('47.00'))
        self.assertEqual(pedido.itens.get().quantidade, 2)

    @patch('apps.catalogo.views_publico.enviar_resumo_whatsapp', return_value=(True, 'Enviado'))
    def test_pix_ignora_troco_e_origem_whatsapp_fica_na_observacao(self, _enviar):
        CatalogoConfiguracao.objects.create(filial=self.filial)
        request = self.factory.post('/pedir/catalogo-teste/', {
            'nome': 'Cliente Pix', 'telefone': '84999994444',
            'modalidade': 'retirada', 'forma_pagamento': 'pix',
            'troco_para': '100,00', 'origem_whatsapp': 'token-valido',
            'observacao': 'Entregar bem embalado.',
            'carrinho_json': json.dumps([{'id': self.produto.pk, 'quantidade': 1}]),
        })
        request.user = AnonymousUser()

        with patch('apps.catalogo.views_publico.conversa_do_token', return_value=object()):
            response = CatalogoPublicoView.as_view()(request, token=self.link.token)

        pedido = PedidoCatalogo.objects.get(nome_cliente='Cliente Pix')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(pedido.troco_para)
        self.assertEqual(
            pedido.observacao,
            'Pedido feito pelo WhatsApp. Entregar bem embalado.',
        )
