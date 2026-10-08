import json
from decimal import Decimal
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from django.contrib.auth.models import AnonymousUser

from apps.core.models import Empresa, Filial
from apps.cadastros.models import Cliente
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial
from apps.whatsapp_agent.agent import processar_mensagem
from apps.whatsapp_agent.models import ConfiguracaoWhatsApp, ConversaWhatsApp

from apps.catalogo.models import CatalogoConfiguracao, CatalogoLinkPublico, PedidoCatalogo
from apps.catalogo.views_publico import CatalogoPublicoView


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
        request = self.factory.get('/pedir/catalogo-teste/')
        request.user = AnonymousUser()
        response = CatalogoPublicoView.as_view()(request, token=self.link.token)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Café 500g', response.content.decode())

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
        response = CatalogoPublicoView.as_view()(request, token=self.link.token)
        self.assertEqual(response.status_code, 200)
        pedido = PedidoCatalogo.objects.get()
        self.assertEqual(pedido.subtotal, Decimal('40.00'))
        self.assertEqual(pedido.valor_frete, Decimal('7.00'))
        self.assertEqual(pedido.total, Decimal('47.00'))
        self.assertEqual(pedido.itens.get().quantidade, 2)
