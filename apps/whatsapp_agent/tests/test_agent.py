from datetime import time, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.agenda.models import (
    AgendaLinkPublico,
    Agendamento,
    AgendamentoItem,
    JornadaTrabalho,
    ProfissionalAgenda,
    ProfissionalServico,
)
from apps.cadastros.models import Funcionario
from apps.core.models import Empresa, Filial, TenantPublicLink
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial
from apps.whatsapp_agent.agent import processar_mensagem
from apps.whatsapp_agent.forms import ConfiguracaoWhatsAppForm
from apps.whatsapp_agent.gateway import EvolutionClient, qr_data_url
from apps.whatsapp_agent.models import ConfiguracaoWhatsApp, ConversaWhatsApp, MensagemWhatsApp
from apps.whatsapp_agent.notifications import enviar_notificacao_agendamento
from apps.whatsapp_agent.webhook import receber_evento


@override_settings(SECRET_KEY='segredo-de-teste-whatsapp')
class AgenteWhatsAppTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Barbearia Bot LTDA', nome_fantasia='Barbearia Bot',
            cnpj='32345678000191', regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Barbearia Bot', nome_fantasia='Barbearia Bot',
            cnpj='32345678000192', uf='CE',
        )
        unidade = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='SV', descricao='Serviço')
        UnidadeMedidaFilial.objects.create(unidade=unidade, filial=cls.filial)
        funcionario = Funcionario.objects.create(filial=cls.filial, nome='Carlos')
        cls.profissional = ProfissionalAgenda.objects.create(filial=cls.filial, funcionario=funcionario)
        cls.servico = Produto.objects.create(
            filial=cls.filial, unidade_medida=unidade, descricao='Corte de cabelo', ncm='00000000',
            tipo_produto=Produto.TipoProduto.SERVICO, agendavel=True,
            duracao_servico_minutos=30, intervalo_apos_servico_minutos=10,
            preco_venda=Decimal('40.00'),
        )
        ProdutoFilial.objects.create(produto=cls.servico, filial=cls.filial)
        ProfissionalServico.objects.create(profissional=cls.profissional, servico=cls.servico)
        JornadaTrabalho.objects.create(
            filial=cls.filial, profissional=cls.profissional, dia_semana=0,
            inicio=time(8), fim=time(12),
        )
        cls.configuracao = ConfiguracaoWhatsApp.objects.create(
            filial=cls.filial, gateway_url='https://evolution.example.com',
            instancia='ited-barbearia-bot', agente_ativo=True,
        )
        cls.configuracao.definir_api_key('chave-ultrassecreta')
        cls.configuracao.save(update_fields=['api_key_criptografada'])

    def nova_conversa(self, telefone='5584999990000', cliente=None):
        return ConversaWhatsApp.objects.create(
            filial=self.filial, configuracao=self.configuracao,
            remote_jid=f'{telefone}@s.whatsapp.net', telefone=telefone, cliente=cliente,
        )

    def test_api_key_fica_criptografada(self):
        self.assertNotIn('chave-ultrassecreta', self.configuracao.api_key_criptografada)
        self.assertEqual(self.configuracao.obter_api_key(), 'chave-ultrassecreta')

    def test_saudacao_exibe_menu_principal(self):
        conversa = self.nova_conversa()

        resposta = processar_mensagem(conversa, 'Olá')

        self.assertIn('*1.* Fazer um agendamento', resposta)
        self.assertIn('*2.* Falar com um atendente', resposta)
        self.assertIn('*3.* Encerrar a conversa', resposta)
        conversa.refresh_from_db()
        self.assertEqual(conversa.etapa, 'aguardando_opcao')

    @override_settings(PUBLIC_BASE_URL='https://ited.app.br')
    def test_opcao_um_cria_e_envia_link_publico_da_agenda(self):
        conversa = self.nova_conversa()
        self.servico.agendavel = False
        self.servico.save(update_fields=['agendavel'])

        resposta = processar_mensagem(conversa, '1')

        link = AgendaLinkPublico.objects.get(filial=self.filial)
        self.assertIn(f'https://ited.app.br/agendar/{link.token}/', resposta)
        self.assertTrue(TenantPublicLink.objects.filter(tipo='agenda').exists())

    def test_opcao_dois_transfere_e_silencia_automacao(self):
        conversa = self.nova_conversa()

        resposta = processar_mensagem(conversa, '2')

        self.assertEqual(resposta, self.configuracao.mensagem_transferencia)
        conversa.refresh_from_db()
        self.assertTrue(conversa.atendimento_humano)
        self.assertEqual(conversa.etapa, 'atendimento_humano')
        self.assertIsNone(processar_mensagem(conversa, 'oi'))

    @patch('apps.whatsapp_agent.agent._destino_agenda', return_value=(None, None))
    def test_opcao_um_nao_envia_link_quebrado_se_tenant_estiver_indisponivel(self, _destino):
        conversa = self.nova_conversa()

        resposta = processar_mensagem(conversa, '1')

        self.assertNotIn('/agendar/', resposta)
        self.assertIn('responda *2*', resposta)

    def test_opcao_tres_encerra_e_novo_oi_reabre_conversa(self):
        conversa = self.nova_conversa()

        resposta = processar_mensagem(conversa, '3')

        self.assertIn('Conversa encerrada', resposta)
        conversa.refresh_from_db()
        self.assertFalse(conversa.ativa)
        self.assertEqual(conversa.etapa, 'encerrada')

        nova_resposta = processar_mensagem(conversa, 'oi')

        self.assertIn('*1.* Fazer um agendamento', nova_resposta)
        conversa.refresh_from_db()
        self.assertTrue(conversa.ativa)

    @patch('apps.whatsapp_agent.notifications.EvolutionClient.enviar_texto')
    def test_confirmacao_do_agendamento_e_enviada_e_registrada(self, enviar_texto):
        enviar_texto.return_value = {'key': {'id': 'confirmacao-1'}}
        self.configuracao.status = ConfiguracaoWhatsApp.Status.CONECTADO
        self.configuracao.save(update_fields=['status'])
        inicio = timezone.now() + timedelta(days=1)
        agendamento = Agendamento.objects.create(
            filial=self.filial,
            profissional=self.profissional,
            pessoa_atendida_nome='Ana Cliente',
            telefone_contato='84999990000',
            inicio=inicio,
            fim=inicio + timedelta(minutes=30),
            fim_com_intervalo=inicio + timedelta(minutes=40),
            valor_total=Decimal('40.00'),
        )
        AgendamentoItem.objects.create(
            agendamento=agendamento,
            servico=self.servico,
            descricao=self.servico.descricao,
            duracao_minutos=30,
            intervalo_minutos=10,
            valor=Decimal('40.00'),
        )

        enviado, mensagem = enviar_notificacao_agendamento(
            agendamento,
            db_alias='default',
        )

        self.assertTrue(enviado)
        self.assertIn('Confirmação enviada', mensagem)
        telefone, texto = enviar_texto.call_args.args
        self.assertEqual(telefone, '5584999990000')
        self.assertIn('Seu agendamento foi confirmado', texto)
        self.assertIn('Corte de cabelo', texto)
        saida = MensagemWhatsApp.objects.get(tipo='confirmacao_agendamento')
        self.assertEqual(saida.status, 'enviada')
        self.assertEqual(saida.identificador_externo, 'confirmacao-1')

        enviar_texto.reset_mock()
        enviar_texto.return_value = {'key': {'id': 'lembrete-1'}}
        enviar_notificacao_agendamento(
            agendamento,
            db_alias='default',
            lembrete=True,
        )
        self.assertIn('Este é um lembrete do seu agendamento', enviar_texto.call_args.args[1])
        self.assertTrue(MensagemWhatsApp.objects.filter(tipo='lembrete_agendamento').exists())

    @patch(
        'apps.whatsapp_agent.notifications._enviar_notificacao_agendamento',
        side_effect=RuntimeError('indisponível'),
    )
    def test_falha_inesperada_do_whatsapp_nao_interrompe_agendamento(self, _enviar):
        enviado, mensagem = enviar_notificacao_agendamento(
            Mock(),
            db_alias='default',
        )

        self.assertFalse(enviado)
        self.assertIn('Não foi possível', mensagem)

    @patch('apps.whatsapp_agent.webhook.EvolutionClient.enviar_texto')
    def test_webhook_e_idempotente(self, enviar_texto):
        enviar_texto.return_value = {'key': {'id': 'saida-1'}}
        payload = {
            'event': 'messages.upsert', 'instance': self.configuracao.instancia,
            'data': {
                'key': {'id': 'entrada-1', 'fromMe': False, 'remoteJid': '5584999992222@s.whatsapp.net'},
                'pushName': 'Ana', 'message': {'conversation': 'Oi'},
            },
        }

        self.assertEqual(receber_evento(self.configuracao, payload), 'enviada')
        self.assertEqual(receber_evento(self.configuracao, payload), 'duplicado')

        conversa = ConversaWhatsApp.objects.get(telefone='5584999992222')
        self.assertEqual(conversa.mensagens.filter(direcao=MensagemWhatsApp.Direcao.ENTRADA).count(), 1)
        self.assertEqual(conversa.mensagens.filter(direcao=MensagemWhatsApp.Direcao.SAIDA).count(), 1)
        enviar_texto.assert_called_once()


class EvolutionClientTests(SimpleTestCase):
    def configuracao(self):
        configuracao = Mock()
        configuracao.gateway_url = 'https://evolution.example.com/'
        configuracao.instancia = 'ited-teste'
        configuracao.obter_api_key.return_value = 'chave-api'
        return configuracao

    @patch('apps.whatsapp_agent.gateway.requests.request')
    def test_cria_instancia_com_webhook_e_eventos(self, requisicao):
        resposta = Mock(status_code=201)
        resposta.json.return_value = {'instance': {'instanceName': 'ited-teste'}}
        requisicao.return_value = resposta

        EvolutionClient(self.configuracao()).criar_instancia('https://ited.app.br/whatsapp/webhook/segredo/')

        metodo, url = requisicao.call_args.args
        payload = requisicao.call_args.kwargs['json']
        self.assertEqual((metodo, url), ('POST', 'https://evolution.example.com/instance/create'))
        self.assertEqual(payload['integration'], 'WHATSAPP-BAILEYS')
        self.assertIn('MESSAGES_UPSERT', payload['webhook']['events'])
        self.assertEqual(requisicao.call_args.kwargs['headers']['apikey'], 'chave-api')

    @patch('apps.whatsapp_agent.gateway.requests.request')
    def test_envia_texto_para_endpoint_da_instancia(self, requisicao):
        resposta = Mock(status_code=200)
        resposta.json.return_value = {'key': {'id': 'mensagem-1'}}
        requisicao.return_value = resposta

        retorno = EvolutionClient(self.configuracao()).enviar_texto('5584999990000', 'Olá')

        self.assertEqual(retorno['key']['id'], 'mensagem-1')
        self.assertEqual(
            requisicao.call_args.args,
            ('POST', 'https://evolution.example.com/message/sendText/ited-teste'),
        )
        self.assertEqual(requisicao.call_args.kwargs['json']['number'], '5584999990000')

    @patch('apps.whatsapp_agent.gateway.requests.request')
    def test_configura_webhook_no_formato_da_evolution_23(self, requisicao):
        resposta = Mock(status_code=201)
        resposta.json.return_value = {'webhook': {'enabled': True}}
        requisicao.return_value = resposta

        EvolutionClient(self.configuracao()).configurar_webhook('https://ited.app.br/webhook/')

        payload = requisicao.call_args.kwargs['json']
        self.assertEqual(payload['webhook']['url'], 'https://ited.app.br/webhook/')
        self.assertFalse(payload['webhook']['byEvents'])
        self.assertIn('MESSAGES_UPSERT', payload['webhook']['events'])
        self.assertNotIn('url', payload)

    def test_gera_imagem_do_qr_code(self):
        resultado = qr_data_url({'code': 'conteudo-do-qr'})
        self.assertTrue(resultado.startswith('data:image/png;base64,'))

    @override_settings(
        WHATSAPP_EVOLUTION_URL='https://gateway-central.example.com/',
        WHATSAPP_EVOLUTION_API_KEY='chave-central',
    )
    def test_credenciais_centrais_substituem_configuracao_do_cliente(self):
        cliente = EvolutionClient(self.configuracao())

        self.assertEqual(cliente.base_url, 'https://gateway-central.example.com')
        self.assertEqual(cliente.api_key, 'chave-central')

    def test_formulario_nao_expoe_credenciais_tecnicas(self):
        self.assertEqual(
            list(ConfiguracaoWhatsAppForm().fields),
            ['agente_ativo', 'mensagem_saudacao', 'mensagem_transferencia', 'ativo'],
        )
