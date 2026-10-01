from datetime import time, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.agenda.models import Agendamento, JornadaTrabalho, ProfissionalAgenda, ProfissionalServico
from apps.cadastros.models import Cliente, Funcionario
from apps.core.models import Empresa, Filial
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial
from apps.whatsapp_agent.agent import processar_mensagem
from apps.whatsapp_agent.forms import ConfiguracaoWhatsAppForm
from apps.whatsapp_agent.gateway import EvolutionClient, qr_data_url
from apps.whatsapp_agent.models import ConfiguracaoWhatsApp, ConversaWhatsApp, MensagemWhatsApp
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

    def proxima_segunda(self):
        hoje = timezone.localdate()
        return hoje + timedelta(days=7 - hoje.weekday())

    def test_api_key_fica_criptografada(self):
        self.assertNotIn('chave-ultrassecreta', self.configuracao.api_key_criptografada)
        self.assertEqual(self.configuracao.obter_api_key(), 'chave-ultrassecreta')

    def test_fluxo_completo_cadastra_cliente_e_agenda(self):
        conversa = self.nova_conversa()

        self.assertIn('Escolha', processar_mensagem(conversa, 'Olá'))
        self.assertIn('Carlos', processar_mensagem(conversa, '1'))
        self.assertIn('Horários disponíveis', processar_mensagem(conversa, self.proxima_segunda().strftime('%d/%m')))
        self.assertIn('qual é o seu nome', processar_mensagem(conversa, '1'))
        self.assertIn('Confirma', processar_mensagem(conversa, 'Diego'))
        resposta = processar_mensagem(conversa, 'sim')

        self.assertIn('Agendamento confirmado', resposta)
        cliente = Cliente.objects.get(celular='5584999990000')
        agendamento = Agendamento.objects.get(cliente=cliente)
        self.assertEqual(agendamento.origem, Agendamento.Origem.WHATSAPP_NAO_OFICIAL)
        self.assertEqual(agendamento.pessoa_atendida_nome, 'Diego')

    def test_confirma_ou_corrige_nome_do_cliente_existente(self):
        cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='F', razao_social='Nome Antigo',
            celular='5584999991111',
        )
        conversa = self.nova_conversa('5584999991111', cliente=cliente)
        processar_mensagem(conversa, 'oi')
        processar_mensagem(conversa, '1')
        processar_mensagem(conversa, self.proxima_segunda().strftime('%d/%m'))

        pergunta = processar_mensagem(conversa, '1')
        self.assertIn('Nome Antigo', pergunta)
        confirmacao = processar_mensagem(conversa, 'Maria Silva')
        self.assertIn('Maria Silva', confirmacao)
        processar_mensagem(conversa, 'sim')

        cliente.refresh_from_db()
        self.assertEqual(cliente.razao_social, 'Maria Silva')

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
