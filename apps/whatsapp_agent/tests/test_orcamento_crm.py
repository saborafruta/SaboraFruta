import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Permissao, Usuario
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import CondicaoPagamento, FormaPagamento
from apps.crm.models import CarteiraCliente
from apps.produtos.models import (
    CategoriaProduto, CategoriaProdutoFilial, ItemTabelaPreco, Produto,
    ProdutoApresentacao, ProdutoFilial, TabelaPreco, TabelaPrecoFilial, UnidadeMedida,
    UnidadeMedidaFilial,
)
from apps.vendas.models import PedidoVenda
from apps.whatsapp_agent.models import (
    ConexaoWhatsAppVendedor, ConversaWhatsApp, MensagemWhatsApp,
    OrcamentoWhatsApp,
)


@override_settings(
    WHATSAPP_EVOLUTION_URL='https://evolution.example.com',
    WHATSAPP_EVOLUTION_API_KEY='chave-teste',
)
class OrcamentoCRMTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Orçamento CRM', cnpj='99111222000144',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz Orçamento CRM',
            nome_fantasia='Matriz', cnpj='99111222000145', uf='RN',
            cidade='Natal', is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Vendedor CRM', ativo=True,
        )
        Permissao.objects.create(
            perfil=cls.perfil, modulo='crm', pode_ver=True,
            pode_criar=True, pode_editar=True,
        )
        cls.vendedor = Usuario.objects.create_user(
            email='orcamento@example.com', nome='Vendedor Orçamento',
            password='teste1234', empresa=cls.empresa, filial=cls.filial,
            perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.categoria = CategoriaProduto.objects.create(
            empresa=cls.empresa, filial=cls.filial, nome='Congelados',
        )
        CategoriaProdutoFilial.objects.create(categoria=cls.categoria, filial=cls.filial)
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade, categoria=cls.categoria,
            descricao='Polpa de acerola', codigo='ACE-01', ncm='20089900',
            preco_venda=Decimal('10.00'), preco_custo=Decimal('4.00'),
            controla_lote=False, permite_venda_sem_estoque=True,
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)
        cls.tabela = TabelaPreco.objects.create(
            filial=cls.filial, descricao='Carteira CRM', tipo=TabelaPreco.Tipo.ATACADO,
        )
        TabelaPrecoFilial.objects.create(tabela=cls.tabela, filial=cls.filial)
        ItemTabelaPreco.objects.create(
            tabela=cls.tabela, produto=cls.produto,
            preco_unitario=Decimal('8.00'), quantidade_minima=0,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, tipo_pessoa='J', razao_social='Mercado do Cliente',
            nome_fantasia='Mercado do Cliente', cpf_cnpj='99111222000146',
            endereco='Rua das Flores', numero='50', bairro='Centro',
            cidade='Natal', uf='RN', tabela_preco=cls.tabela,
            limite_credito=Decimal('1000'), ativo=True,
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.forma = FormaPagamento.objects.create(
            empresa=cls.empresa, filial=cls.filial, descricao='PIX',
            tipo=TipoFormaPagamento.PIX,
        )
        cls.condicao = CondicaoPagamento.objects.create(
            empresa=cls.empresa, descricao='À vista', numero_parcelas=1,
        )
        cls.conexao = ConexaoWhatsAppVendedor.objects.create(
            empresa=cls.empresa, filial_padrao=cls.filial, vendedor=cls.vendedor,
            numero_conectado='5584991112222',
            status=ConexaoWhatsAppVendedor.Status.CONECTADO,
        )

    def setUp(self):
        self.conversa = ConversaWhatsApp.objects.create(
            filial=self.filial, conexao_vendedor=self.conexao,
            cliente=self.cliente, cliente_operacional_id=self.cliente.pk,
            cliente_grupo_replicacao=self.cliente.grupo_replicacao,
            remote_jid='5584993334444@s.whatsapp.net', telefone='5584993334444',
            nome_contato='Mercado do Cliente', fora_carteira=False,
            ultima_mensagem_cliente_em=timezone.now(),
        )
        self.client.force_login(self.vendedor)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

    def _payload(self):
        return {
            'itens': [{
                'produto_id': self.produto.pk, 'quantidade': '2',
                # O servidor recalcula pela tabela; este valor adulterado é ignorado.
                'valor_unitario': '0.01', 'desconto_percentual': '10',
            }],
            'forma_pagamento': self.forma.pk,
            'condicao_pagamento': self.condicao.pk,
            'validade': '2027-01-15',
            'endereco': 'Rua das Flores, 50, Centro, Natal/RN',
            'observacao': 'Entregar pela manhã.',
            'valor_frete': '3.00',
            'desconto_geral_tipo': 'valor',
            'desconto_geral_valor': '2.00',
        }

    def _salvar(self):
        return self.client.post(
            reverse('whatsapp_agent:crm-orcamento-salvar', args=[self.conversa.pk]),
            data=json.dumps(self._payload()), content_type='application/json',
        )

    @staticmethod
    def _pedido(orcamento):
        return PedidoVenda.objects.get(pk=orcamento.pedido_operacional_id)

    def _resposta_publica(self, orcamento, dados, *, cliente=None, **extra):
        cliente = cliente or self.client
        url = reverse(
            'orcamento_publico:detalhe', args=[orcamento.token_publico],
        )
        pagina = cliente.get(url)
        self.assertEqual(pagina.status_code, 200)
        return cliente.post(
            url,
            {**dados, 'resposta_token': pagina.context['resposta_token']},
            **extra,
        )

    def test_editor_busca_preco_da_tabela_do_cliente(self):
        inbox = self.client.get(
            reverse('whatsapp_agent:crm-inbox-detail', args=[self.conversa.pk]),
        )
        editor = self.client.get(
            reverse('whatsapp_agent:crm-orcamento-editor', args=[self.conversa.pk]),
        )
        busca = self.client.get(
            reverse('whatsapp_agent:crm-orcamento-produtos', args=[self.conversa.pk]),
            {'q': 'acerola'},
        )
        busca_codigo = self.client.get(
            reverse('whatsapp_agent:crm-orcamento-produtos', args=[self.conversa.pk]),
            {'q': 'A'},
        )

        self.assertContains(inbox, 'Orçamento')
        self.assertEqual(editor.status_code, 200)
        self.assertEqual(editor.json()['cliente']['nome'], 'Mercado do Cliente')
        self.assertEqual(busca.json()['produtos'][0]['preco'], '8.0000')
        self.assertEqual(busca_codigo.json()['produtos'][0]['codigo'], 'ACE-01')

    def test_pode_trocar_cliente_do_orcamento_dentro_da_carteira(self):
        outro_cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='J', razao_social='Grupo Cliente Filial Sul',
            nome_fantasia='Filial Sul', cpf_cnpj='99111222000147',
            celular='84988887777', endereco='Avenida Sul', numero='200',
            bairro='Distrito', cidade='Natal', uf='RN', tabela_preco=self.tabela,
            limite_credito=Decimal('2000'), ativo=True,
        )
        ClienteFilial.objects.create(cliente=outro_cliente, filial=self.filial)
        CarteiraCliente.objects.create(
            empresa=self.empresa, cliente=outro_cliente,
            grupo_cliente=outro_cliente.grupo_replicacao,
            vendedor=self.vendedor, atribuido_por=self.vendedor,
        )
        clientes_url = reverse(
            'whatsapp_agent:crm-orcamento-clientes', args=[self.conversa.pk],
        )
        busca_cliente = self.client.get(clientes_url, {'q': 'Filial Sul'})
        self.assertEqual(busca_cliente.status_code, 200)
        self.assertEqual(busca_cliente.json()['clientes'][0]['id'], outro_cliente.pk)

        produtos_url = reverse(
            'whatsapp_agent:crm-orcamento-produtos', args=[self.conversa.pk],
        )
        busca_produto = self.client.get(produtos_url, {
            'q': 'acerola', 'cliente_id': outro_cliente.pk,
        })
        self.assertEqual(busca_produto.status_code, 200)

        payload = self._payload()
        payload['cliente_id'] = outro_cliente.pk
        resposta = self.client.post(
            reverse('whatsapp_agent:crm-orcamento-salvar', args=[self.conversa.pk]),
            data=json.dumps(payload), content_type='application/json',
        )

        self.assertEqual(resposta.status_code, 200, resposta.content)
        orcamento = OrcamentoWhatsApp.objects.get()
        pedido = self._pedido(orcamento)
        self.assertEqual(pedido.cliente, outro_cliente)
        self.assertEqual(resposta.json()['orcamento']['cliente']['id'], outro_cliente.pk)

    def test_nao_permite_cliente_fora_da_carteira_no_orcamento(self):
        outro_cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='J', razao_social='Cliente de outro vendedor',
            cpf_cnpj='99111222000148', ativo=True,
        )
        ClienteFilial.objects.create(cliente=outro_cliente, filial=self.filial)
        payload = self._payload()
        payload['cliente_id'] = outro_cliente.pk

        resposta = self.client.post(
            reverse('whatsapp_agent:crm-orcamento-salvar', args=[self.conversa.pk]),
            data=json.dumps(payload), content_type='application/json',
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertIn('carteira', resposta.json()['erro'])

    def test_busca_localiza_produto_por_id_e_por_embalagem(self):
        apresentacao = ProdutoApresentacao.objects.create(
            produto=self.produto, unidade=self.unidade,
            descricao='Caixa com 12 unidades', codigo='CX-12',
            fator_conversao=Decimal('12'), preco_venda=Decimal('60'),
            permite_venda=True,
        )
        url = reverse(
            'whatsapp_agent:crm-orcamento-produtos', args=[self.conversa.pk],
        )

        por_id = self.client.get(url, {'q': str(self.produto.pk)})
        por_embalagem = self.client.get(url, {'q': 'CX-12'})

        self.assertEqual(por_id.status_code, 200)
        self.assertEqual(por_id.json()['produtos'][0]['id'], self.produto.pk)
        self.assertEqual(len(por_id.json()['produtos']), 2)
        self.assertEqual(por_embalagem.status_code, 200)
        self.assertEqual(len(por_embalagem.json()['produtos']), 2)
        embalagem = next(
            item for item in por_embalagem.json()['produtos']
            if item['apresentacao_id'] == apresentacao.pk
        )
        self.assertEqual(embalagem['descricao'], 'Polpa de acerola — Caixa com 12 unidades')
        self.assertEqual(embalagem['preco'], '60.0000')

    def test_edita_orcamento_enviado_e_adiciona_embalagem(self):
        apresentacao = ProdutoApresentacao.objects.create(
            produto=self.produto, unidade=self.unidade,
            descricao='Caixa com 12 unidades', codigo='CX-12',
            fator_conversao=Decimal('12'), preco_venda=Decimal('60'),
            permite_venda=True,
        )
        self._salvar()
        orcamento = OrcamentoWhatsApp.objects.get()
        orcamento.status = OrcamentoWhatsApp.Status.ENVIADO
        orcamento.save(update_fields=['status'])
        payload = self._payload()
        payload['orcamento_id'] = orcamento.pk
        payload['itens'].append({
            'produto_id': self.produto.pk,
            'apresentacao_id': apresentacao.pk,
            'quantidade': '2',
            'valor_unitario': '0.01',
            'desconto_percentual': '0',
        })

        resposta = self.client.post(
            reverse('whatsapp_agent:crm-orcamento-salvar', args=[self.conversa.pk]),
            data=json.dumps(payload), content_type='application/json',
        )

        self.assertEqual(resposta.status_code, 200, resposta.content)
        pedido = self._pedido(orcamento)
        self.assertEqual(pedido.itens.count(), 2)
        item = pedido.itens.get(apresentacao=apresentacao)
        self.assertEqual(item.quantidade, Decimal('24.000'))
        self.assertEqual(item.quantidade_comercial, Decimal('2.000'))
        self.assertEqual(item.valor_unitario_comercial, Decimal('60.0000'))
        serializado = resposta.json()['orcamento']['itens'][1]
        self.assertEqual(serializado['quantidade'], '2.000')
        self.assertEqual(serializado['valor_unitario'], '60.0000')
        with (
            patch('apps.whatsapp_agent.views.TenantPublicLinkService.register'),
            patch(
                'apps.whatsapp_agent.views.EvolutionClient.enviar_texto',
                return_value={'key': {'id': 'orcamento-embalagem-1'}},
            ) as enviar,
        ):
            envio = self.client.post(reverse(
                'whatsapp_agent:crm-orcamento-enviar',
                args=[self.conversa.pk, orcamento.pk],
            ))
        self.assertEqual(envio.status_code, 200, envio.content)
        self.assertIn('Caixa com 12 unidades', enviar.call_args.args[1])

    @patch('apps.whatsapp_agent.views.salvar_orcamento', side_effect=RuntimeError('falha'))
    def test_falha_inesperada_ao_salvar_ainda_retorna_json(self, salvar):
        resposta = self.client.post(
            reverse('whatsapp_agent:crm-orcamento-salvar', args=[self.conversa.pk]),
            data=json.dumps(self._payload()), content_type='application/json',
        )

        self.assertEqual(resposta.status_code, 500)
        self.assertEqual(resposta['Content-Type'], 'application/json')
        self.assertIn('erro', resposta.json())

    @patch(
        'apps.whatsapp_agent.views.TenantPublicLinkService.register',
        side_effect=RuntimeError('falha'),
    )
    def test_falha_inesperada_ao_enviar_ainda_retorna_json(self, register):
        self._salvar()
        orcamento = OrcamentoWhatsApp.objects.get()

        resposta = self.client.post(reverse(
            'whatsapp_agent:crm-orcamento-enviar',
            args=[self.conversa.pk, orcamento.pk],
        ))

        self.assertEqual(resposta.status_code, 500)
        self.assertEqual(resposta['Content-Type'], 'application/json')
        self.assertIn('erro', resposta.json())

    def test_conversa_consolidada_retorna_json_em_vez_de_pagina_html(self):
        self.conversa.ativa = False
        self.conversa.save(update_fields=['ativa'])

        resposta = self.client.post(
            reverse('whatsapp_agent:crm-orcamento-salvar', args=[self.conversa.pk]),
            data=json.dumps(self._payload()), content_type='application/json',
        )

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(resposta['Content-Type'], 'application/json')
        self.assertIn('Recarregue', resposta.json()['erro'])

    def test_salva_itens_descontos_entrega_e_pagamento(self):
        resposta = self._salvar()

        self.assertEqual(resposta.status_code, 200, resposta.content)
        orcamento = OrcamentoWhatsApp.objects.get()
        pedido = self._pedido(orcamento)
        self.assertEqual(pedido.tipo, PedidoVenda.Tipo.ORCAMENTO)
        self.assertEqual(pedido.origem, PedidoVenda.Origem.WHATSAPP)
        self.assertEqual(pedido.valor_produtos, Decimal('16.00'))
        self.assertEqual(pedido.valor_desconto, Decimal('3.60'))
        self.assertEqual(pedido.valor_total, Decimal('15.40'))
        self.assertEqual(pedido.forma_pagamento, self.forma)
        self.assertEqual(pedido.endereco_entrega_avulso['descricao'], 'Rua das Flores, 50, Centro, Natal/RN')

    @patch('apps.whatsapp_agent.views.TenantPublicLinkService.register')
    @patch('apps.whatsapp_agent.views.EvolutionClient.enviar_texto')
    def test_envio_humano_manda_link_e_cliente_aprova_pedido(self, enviar, registrar):
        enviar.return_value = {'key': {'id': 'orcamento-enviado-1'}}
        self._salvar()
        orcamento = OrcamentoWhatsApp.objects.get()

        resposta = self.client.post(reverse(
            'whatsapp_agent:crm-orcamento-enviar',
            args=[self.conversa.pk, orcamento.pk],
        ))

        self.assertEqual(resposta.status_code, 200, resposta.content)
        registrar.assert_called_once()
        texto = enviar.call_args.args[1]
        self.assertIn('/orcamento/', texto)
        self.assertIn('Polpa de acerola', texto)
        self.assertTrue(MensagemWhatsApp.objects.filter(origem='crm').exists())
        orcamento.refresh_from_db()
        self.assertEqual(orcamento.status, OrcamentoWhatsApp.Status.ENVIADO)

        aprovacao = self._resposta_publica(
            orcamento, {'acao': 'aprovar'},
        )
        self.assertEqual(aprovacao.status_code, 200)
        orcamento.refresh_from_db()
        pedido = self._pedido(orcamento)
        self.assertEqual(orcamento.status, OrcamentoWhatsApp.Status.APROVADO)
        self.assertEqual(pedido.tipo, PedidoVenda.Tipo.PEDIDO)
        self.assertEqual(pedido.status, PedidoVenda.Status.APROVADO)

    def test_cliente_recusa_e_motivo_fica_registrado(self):
        self._salvar()
        orcamento = OrcamentoWhatsApp.objects.get()
        orcamento.status = OrcamentoWhatsApp.Status.ENVIADO
        orcamento.save(update_fields=['status'])
        pedido = self._pedido(orcamento)
        pedido.status = PedidoVenda.Status.AGUARDANDO_APROVACAO
        pedido.save(update_fields=['status'])

        resposta = self._resposta_publica(
            orcamento, {
                'acao': 'recusar',
                'motivo': 'Preciso de outro prazo de pagamento.',
            },
        )

        self.assertEqual(resposta.status_code, 200)
        orcamento.refresh_from_db()
        pedido.refresh_from_db()
        self.assertEqual(orcamento.status, OrcamentoWhatsApp.Status.RECUSADO)
        self.assertIn('outro prazo', orcamento.motivo_recusa)
        self.assertEqual(pedido.status, PedidoVenda.Status.CANCELADO)

    def test_aprovacao_funciona_no_navegador_whatsapp_com_origin_null(self):
        self._salvar()
        orcamento = OrcamentoWhatsApp.objects.get()
        orcamento.status = OrcamentoWhatsApp.Status.ENVIADO
        orcamento.save(update_fields=['status'])
        pedido = self._pedido(orcamento)
        pedido.status = PedidoVenda.Status.AGUARDANDO_APROVACAO
        pedido.save(update_fields=['status'])
        navegador_whatsapp = Client(enforce_csrf_checks=True)

        resposta = self._resposta_publica(
            orcamento, {'acao': 'aprovar'},
            cliente=navegador_whatsapp, secure=True, HTTP_ORIGIN='null',
        )

        self.assertEqual(resposta.status_code, 200, resposta.content)
        orcamento.refresh_from_db()
        pedido.refresh_from_db()
        self.assertEqual(orcamento.status, OrcamentoWhatsApp.Status.APROVADO)
        self.assertEqual(pedido.tipo, PedidoVenda.Tipo.PEDIDO)
        self.assertEqual(pedido.status, PedidoVenda.Status.APROVADO)
        self.assertEqual(pedido.origem, PedidoVenda.Origem.WHATSAPP)

    def test_resposta_publica_recusa_token_assinado_invalido(self):
        self._salvar()
        orcamento = OrcamentoWhatsApp.objects.get()
        orcamento.status = OrcamentoWhatsApp.Status.ENVIADO
        orcamento.save(update_fields=['status'])

        resposta = self.client.post(
            reverse('orcamento_publico:detalhe', args=[orcamento.token_publico]),
            {'acao': 'aprovar', 'resposta_token': 'assinatura-invalida'},
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Confirme sua resposta novamente')
        orcamento.refresh_from_db()
        self.assertEqual(orcamento.status, OrcamentoWhatsApp.Status.ENVIADO)

    def test_orcamento_enviado_pode_ser_editado_e_volta_a_rascunho(self):
        self._salvar()
        orcamento = OrcamentoWhatsApp.objects.get()
        orcamento.status = OrcamentoWhatsApp.Status.ENVIADO
        orcamento.save(update_fields=['status'])
        pedido = self._pedido(orcamento)
        pedido.status = PedidoVenda.Status.AGUARDANDO_APROVACAO
        pedido.save(update_fields=['status'])
        payload = self._payload()
        payload['orcamento_id'] = orcamento.pk
        payload['observacao'] = 'Versão revisada pelo vendedor.'

        resposta = self.client.post(
            reverse('whatsapp_agent:crm-orcamento-salvar', args=[self.conversa.pk]),
            data=json.dumps(payload), content_type='application/json',
        )

        self.assertEqual(resposta.status_code, 200, resposta.content)
        orcamento.refresh_from_db()
        pedido.refresh_from_db()
        self.assertEqual(orcamento.status, OrcamentoWhatsApp.Status.RASCUNHO)
        self.assertEqual(pedido.status, PedidoVenda.Status.RASCUNHO)
        self.assertEqual(pedido.observacao, 'Versão revisada pelo vendedor.')

    @patch('apps.whatsapp_agent.gateway.MetaCloudClient.enviar_template')
    @patch('apps.whatsapp_agent.views.EvolutionClient.enviar_texto')
    def test_envio_humano_do_orcamento_funciona_apos_24h_sem_template(self, enviar, template):
        enviar.return_value = {'key': {'id': 'orcamento-manual-24h'}}
        self._salvar()
        orcamento = OrcamentoWhatsApp.objects.get()
        self.conversa.ultima_mensagem_cliente_em = timezone.now() - timedelta(hours=25)
        self.conversa.save(update_fields=['ultima_mensagem_cliente_em'])

        resposta = self.client.post(reverse(
            'whatsapp_agent:crm-orcamento-enviar',
            args=[self.conversa.pk, orcamento.pk],
        ))

        self.assertEqual(resposta.status_code, 200, resposta.content)
        enviar.assert_called_once()
        template.assert_not_called()
