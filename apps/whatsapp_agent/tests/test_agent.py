from datetime import time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django import forms
from django.test import SimpleTestCase, TestCase, override_settings
from django.template.loader import render_to_string
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
from apps.catalogo.models import CatalogoConfiguracao, PedidoCatalogo
from apps.core.models import Empresa, Filial, Notificacao, TenantPublicLink
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial
from apps.whatsapp_agent.agent import processar_mensagem
from apps.whatsapp_agent.ai_service import IAWhatsAppError, OpenAIWhatsAppClient
from apps.whatsapp_agent.conversation_service import (
    encerrar_conversa,
    encerrar_conversas_inativas,
)
from apps.whatsapp_agent.crm_service import recuperar_agendamentos_abandonados
from apps.whatsapp_agent.forms import ConfiguracaoWhatsAppForm, FluxoWhatsAppForm
from apps.whatsapp_agent.gateway import (
    EvolutionClient, GatewayWhatsAppError, MetaCloudClient, qr_data_url,
)
from apps.whatsapp_agent.intelligent_agent import processar_com_ia
from apps.whatsapp_agent.models import (
    ConfiguracaoWhatsApp,
    ConversaWhatsApp,
    MensagemWhatsApp,
    MenuWhatsApp,
    OpcaoMenuWhatsApp,
    UsoIAWhatsApp,
)
from apps.whatsapp_agent.notifications import _numero_whatsapp, enviar_notificacao_agendamento
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

    def test_normaliza_nono_digito_de_celular_brasileiro(self):
        self.assertEqual(_numero_whatsapp('(89) 9623-4025'), '5589996234025')
        self.assertEqual(_numero_whatsapp('55 89 9623-4025'), '5589996234025')
        self.assertEqual(_numero_whatsapp('(89) 99623-4025'), '5589996234025')
        self.assertEqual(_numero_whatsapp('(89) 3222-0000'), '558932220000')

    def test_api_key_fica_criptografada(self):
        self.assertNotIn('chave-ultrassecreta', self.configuracao.api_key_criptografada)
        self.assertEqual(self.configuracao.obter_api_key(), 'chave-ultrassecreta')

    @override_settings(OPENAI_API_KEY='')
    def test_chave_da_ia_fica_criptografada_por_filial(self):
        self.configuracao.definir_ia_api_key('sk-chave-da-filial')
        self.configuracao.save(update_fields=['ia_api_key_criptografada'])

        self.assertNotIn('sk-chave-da-filial', self.configuracao.ia_api_key_criptografada)
        self.assertEqual(self.configuracao.obter_ia_api_key(), 'sk-chave-da-filial')
        self.assertTrue(self.configuracao.ia_api_key_configurada)

    @patch('apps.whatsapp_agent.ai_service.requests.post')
    def test_registra_tokens_e_custo_estimado_da_resposta(self, requisicao):
        self.configuracao.definir_ia_api_key('sk-teste')
        self.configuracao.ia_cotacao_dolar = Decimal('5.00')
        self.configuracao.save(update_fields=['ia_api_key_criptografada', 'ia_cotacao_dolar'])
        conversa = self.nova_conversa()
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Quero fazer um pedido',
        )
        resposta = Mock(status_code=200)
        resposta.json.return_value = {
            'id': 'resp_1',
            'output': [{
                'type': 'message',
                'content': [{'type': 'output_text', 'text': '{"acao":"abrir_catalogo"}'}],
            }],
            'usage': {
                'input_tokens': 1000,
                'input_tokens_details': {'cached_tokens': 200},
                'output_tokens': 100,
                'total_tokens': 1100,
            },
        }
        requisicao.return_value = resposta

        resultado = OpenAIWhatsAppClient(self.configuracao).responder_estruturado(
            conversa=conversa,
            mensagem=mensagem,
            instrucoes='Responda em JSON.',
            entrada='Pedido',
            schema={'type': 'object'},
        )

        self.assertEqual(resultado.conteudo, {'acao': 'abrir_catalogo'})
        uso = UsoIAWhatsApp.objects.get(conversa=conversa)
        self.assertEqual(uso.tokens_total, 1100)
        self.assertEqual(uso.tokens_entrada_cache, 200)
        self.assertEqual(uso.custo_brl, Decimal('0.00097500'))

    @patch('apps.whatsapp_agent.webhook.processar_com_ia', return_value='Entendi o áudio.')
    @patch('apps.whatsapp_agent.webhook.OpenAIWhatsAppClient.transcrever')
    @patch('apps.whatsapp_agent.webhook.EvolutionClient.obter_midia_base64')
    @patch('apps.whatsapp_agent.webhook.EvolutionClient.enviar_texto')
    def test_audio_e_transcrito_e_respondido_pelo_mesmo_agente(
        self, enviar_texto, obter_midia, transcrever, processar_ia,
    ):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.ia_audio_ativo = True
        self.configuracao.definir_ia_api_key('sk-teste')
        self.configuracao.save(update_fields=[
            'ia_ativa', 'ia_restringir_horario', 'ia_audio_ativo',
            'ia_api_key_criptografada',
        ])
        enviar_texto.return_value = {'key': {'id': 'saida-audio'}}
        obter_midia.return_value = {'base64': 'YXVkaW8=', 'mimetype': 'audio/ogg'}
        transcrever.return_value = SimpleNamespace(conteudo='Quero um corte amanhã à tarde')
        payload = {
            'event': 'messages.upsert',
            'instance': self.configuracao.instancia,
            'data': {
                'key': {
                    'id': 'entrada-audio', 'fromMe': False,
                    'remoteJid': '5584999994444@s.whatsapp.net',
                },
                'pushName': 'Ana',
                'message': {
                    'ephemeralMessage': {
                        'message': {
                            'audioMessage': {
                                'mimetype': 'audio/ogg; codecs=opus', 'ptt': True,
                            },
                        },
                    },
                },
            },
        }

        self.assertEqual(receber_evento(self.configuracao, payload), 'enviada')

        entrada = MensagemWhatsApp.objects.get(identificador_externo='entrada-audio')
        self.assertEqual(entrada.tipo, 'audio')
        self.assertEqual(entrada.status, 'transcrita')
        self.assertEqual(entrada.texto, 'Quero um corte amanhã à tarde')
        processar_ia.assert_called_once_with(
            entrada.conversa, 'Quero um corte amanhã à tarde', entrada,
        )
        enviar_texto.assert_called_once_with('5584999994444', 'Entendi o áudio.')

    @patch('apps.whatsapp_agent.ai_service.requests.post')
    def test_ia_consulta_disponibilidade_e_confirma_agendamento(self, requisicao):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.definir_ia_api_key('sk-teste')
        self.configuracao.save(update_fields=[
            'ia_ativa', 'ia_restringir_horario', 'ia_api_key_criptografada',
        ])
        conversa = self.nova_conversa('5584999995555')
        proxima_segunda = timezone.localdate() + timedelta(
            days=(7 - timezone.localdate().weekday()) % 7 or 7,
        )
        decisoes = [
            {
                'acao': 'consultar_agenda', 'mensagem': '',
                'servico_id': self.servico.pk,
                'profissional_id': self.profissional.pk,
                'data': proxima_segunda.isoformat(), 'periodo': '',
                'horario': '', 'nome_cliente': '',
            },
            {
                'acao': 'agendar', 'mensagem': '',
                'servico_id': self.servico.pk,
                'profissional_id': self.profissional.pk,
                'data': proxima_segunda.isoformat(), 'periodo': 'manha',
                'horario': '08:00', 'nome_cliente': 'Ana Cliente',
                'confirmou_nome': True,
            },
        ]

        def resposta_openai(*_args, **_kwargs):
            decisao = decisoes.pop(0)
            resposta = Mock(status_code=200)
            resposta.json.return_value = {
                'id': 'resp-agenda',
                'output': [{
                    'type': 'message',
                    'content': [{'type': 'output_text', 'text': __import__('json').dumps(decisao)}],
                }],
                'usage': {'input_tokens': 100, 'output_tokens': 30, 'total_tokens': 130},
            }
            return resposta

        requisicao.side_effect = resposta_openai
        mensagem_consulta = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Quero cortar o cabelo na próxima segunda',
        )

        disponibilidade = processar_com_ia(
            conversa, mensagem_consulta.texto, mensagem_consulta,
        )

        self.assertIn('manhã', disponibilidade)
        mensagem_horario = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Pode ser às 08:00, meu nome é Ana Cliente',
        )

        confirmacao = processar_com_ia(conversa, mensagem_horario.texto, mensagem_horario)

        self.assertIn('Seu agendamento foi confirmado', confirmacao)
        agendamento = Agendamento.objects.get(telefone_contato='5584999995555')
        self.assertEqual(agendamento.pessoa_atendida_nome, 'Ana Cliente')
        self.assertEqual(agendamento.origem, Agendamento.Origem.WHATSAPP_NAO_OFICIAL)
        conversa.refresh_from_db()
        self.assertEqual(conversa.etapa_crm, ConversaWhatsApp.EtapaCRM.AGENDADO)
        self.assertEqual(UsoIAWhatsApp.objects.filter(conversa=conversa).count(), 2)

    @patch('apps.whatsapp_agent.intelligent_agent.OpenAIWhatsAppClient.responder_estruturado')
    def test_saudacao_reinicia_contexto_sem_reaproveitar_horarios(self, responder):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.definir_ia_api_key('sk-teste')
        self.configuracao.save(update_fields=[
            'ia_ativa', 'ia_restringir_horario', 'ia_api_key_criptografada',
        ])
        responder.return_value = SimpleNamespace(conteudo={
            'acao': 'responder',
            'mensagem': 'Olá! Como posso ajudar?',
            'servico_id': None,
            'profissional_id': None,
            'data': '',
            'periodo': '',
            'horario': '',
            'nome_cliente': '',
        })
        conversa = self.nova_conversa('5584999996666')
        conversa.contexto = {
            'ia_agenda': {
                'servico_id': self.servico.pk,
                'periodo': 'tarde',
                'horarios_oferecidos': ['14:00'],
            },
        }
        conversa.save(update_fields=['contexto'])
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Oi',
        )

        resposta = processar_com_ia(conversa, mensagem.texto, mensagem)

        self.assertEqual(resposta, 'Olá! Como posso ajudar você hoje?')
        responder.assert_not_called()
        conversa.refresh_from_db()
        self.assertEqual(conversa.etapa, 'ia_atendimento')
        self.assertNotIn('ia_agenda', conversa.contexto)

    @patch('apps.whatsapp_agent.intelligent_agent.OpenAIWhatsAppClient.responder_estruturado')
    def test_ia_oferece_complementos_e_agenda_multiplos_servicos(self, responder):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.definir_ia_api_key('sk-teste')
        self.configuracao.save(update_fields=[
            'ia_ativa', 'ia_restringir_horario', 'ia_api_key_criptografada',
        ])
        barba = Produto.objects.create(
            filial=self.filial,
            unidade_medida=self.servico.unidade_medida,
            descricao='Barba',
            ncm='00000000',
            tipo_produto=Produto.TipoProduto.SERVICO,
            agendavel=True,
            duracao_servico_minutos=15,
            intervalo_apos_servico_minutos=5,
            preco_venda=Decimal('20.00'),
        )
        ProdutoFilial.objects.create(produto=barba, filial=self.filial)
        ProfissionalServico.objects.create(
            profissional=self.profissional,
            servico=barba,
        )
        proxima_segunda = timezone.localdate() + timedelta(
            days=(7 - timezone.localdate().weekday()) % 7 or 7,
        )
        decisoes = [
            {
                'acao': 'consultar_agenda', 'mensagem': '',
                'servico_id': self.servico.pk,
                'servico_ids': [self.servico.pk],
                'confirmou_servicos': False,
                'profissional_id': self.profissional.pk,
                'data': proxima_segunda.isoformat(), 'periodo': 'manha',
                'horario': '', 'nome_cliente': '',
            },
            {
                'acao': 'consultar_agenda', 'mensagem': '',
                'servico_id': barba.pk,
                'servico_ids': [barba.pk],
                'confirmou_servicos': True,
                'profissional_id': self.profissional.pk,
                'data': proxima_segunda.isoformat(), 'periodo': 'manha',
                'horario': '', 'nome_cliente': '',
            },
            {
                'acao': 'agendar', 'mensagem': '',
                'servico_id': self.servico.pk,
                'servico_ids': [self.servico.pk, barba.pk],
                'confirmou_servicos': True,
                'profissional_id': self.profissional.pk,
                'data': proxima_segunda.isoformat(), 'periodo': 'manha',
                'horario': '08:00', 'nome_cliente': 'Ana Cliente',
                'confirmou_nome': True,
            },
        ]
        responder.side_effect = [
            SimpleNamespace(conteudo=decisao) for decisao in decisoes
        ]
        conversa = self.nova_conversa('5584999996677')

        primeira = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Quero corte na segunda de manhã',
        )
        oferta = processar_com_ia(conversa, primeira.texto, primeira)
        self.assertIn('Deseja adicionar também Barba?', oferta)

        segunda = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Sim, barba também',
        )
        horarios = processar_com_ia(conversa, segunda.texto, segunda)
        self.assertIn('08:00', horarios)
        self.assertIn(proxima_segunda.strftime('%d/%m'), horarios)

        terceira = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='08:00, Ana Cliente',
        )
        confirmacao = processar_com_ia(conversa, terceira.texto, terceira)
        self.assertIn('Seu agendamento foi confirmado', confirmacao)
        agendamento = Agendamento.objects.get(telefone_contato='5584999996677')
        self.assertEqual(agendamento.itens.count(), 2)
        self.assertEqual(
            agendamento.fim - agendamento.inicio,
            timedelta(minutes=45),
        )
        conversa.refresh_from_db()
        self.assertFalse(conversa.ativa)
        self.assertEqual(conversa.etapa, 'encerrada')
        self.assertEqual(conversa.etapa_crm, ConversaWhatsApp.EtapaCRM.AGENDADO)

    @patch('apps.whatsapp_agent.intelligent_agent.OpenAIWhatsAppClient.responder_estruturado')
    def test_pedido_generico_de_agendamento_pergunta_periodo_sem_inventar_dados(self, responder):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.save(update_fields=['ia_ativa', 'ia_restringir_horario'])
        conversa = self.nova_conversa('5584999996699')
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Quero agendar',
        )

        resposta = processar_com_ia(conversa, mensagem.texto, mensagem)

        self.assertEqual(resposta, 'Qual período você prefere: manhã, tarde ou noite?')
        responder.assert_not_called()
        conversa.refresh_from_db()
        estado = conversa.contexto['ia_agenda']
        self.assertTrue(estado['fluxo_ativo'])
        self.assertNotIn('data', estado)
        self.assertNotIn('servico_id', estado)

    @patch('apps.whatsapp_agent.intelligent_agent.OpenAIWhatsAppClient.responder_estruturado')
    def test_pergunta_sobre_data_responde_contexto_sem_repetir_horarios(self, responder):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.save(update_fields=['ia_ativa', 'ia_restringir_horario'])
        conversa = self.nova_conversa('5584999996600')
        conversa.contexto = {
            'ia_agenda': {
                'fluxo_ativo': True,
                'data': '2026-10-16',
                'horarios_oferecidos': ['12:00', '12:15'],
            },
        }
        conversa.save(update_fields=['contexto'])
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Pra que dia é?',
        )

        resposta = processar_com_ia(conversa, mensagem.texto, mensagem)

        self.assertIn('sexta-feira, 16/10/2026', resposta)
        self.assertNotIn('12:00', resposta)
        responder.assert_not_called()

    @patch('apps.whatsapp_agent.intelligent_agent.OpenAIWhatsAppClient.responder_estruturado')
    def test_cliente_pode_pedir_datas_disponiveis_sem_tentar_dia_por_dia(self, responder):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.save(update_fields=['ia_ativa', 'ia_restringir_horario'])
        conversa = self.nova_conversa('5584999996611')
        conversa.contexto = {
            'ia_agenda': {
                'fluxo_ativo': True,
                'servico_id': self.servico.pk,
                'servico_ids': [self.servico.pk],
                'complementos_confirmados': True,
                'profissional_id': self.profissional.pk,
                'data': '2026-10-18',
            },
        }
        conversa.save(update_fields=['contexto'])
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Quais datas têm disponível?',
        )
        proxima_segunda = timezone.localdate() + timedelta(
            days=(7 - timezone.localdate().weekday()) % 7 or 7,
        )

        resposta = processar_com_ia(conversa, mensagem.texto, mensagem)

        self.assertIn('encontrei estas próximas datas', resposta)
        self.assertIn(proxima_segunda.strftime('%d/%m'), resposta)
        self.assertIn('manhã', resposta)
        self.assertNotIn('Quer tentar outra data?', resposta)
        responder.assert_not_called()
        conversa.refresh_from_db()
        estado = conversa.contexto['ia_agenda']
        self.assertNotIn('data', estado)
        self.assertIn(proxima_segunda.isoformat(), estado['datas_oferecidas'])

    @patch('apps.whatsapp_agent.intelligent_agent.OpenAIWhatsAppClient.responder_estruturado')
    def test_consulta_de_datas_explica_quando_ainda_falta_servico(self, responder):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.save(update_fields=['ia_ativa', 'ia_restringir_horario'])
        conversa = self.nova_conversa('5584999996622')
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Quando tem vaga?',
        )

        resposta = processar_com_ia(conversa, mensagem.texto, mensagem)

        self.assertIn('Consigo consultar as próximas datas', resposta)
        self.assertIn('qual serviço você deseja', resposta)
        self.assertIn('Corte de cabelo', resposta)
        responder.assert_not_called()

    @patch('apps.whatsapp_agent.intelligent_agent.OpenAIWhatsAppClient.responder_estruturado')
    def test_ia_pode_identificar_outra_forma_de_pedir_agenda_livre(self, responder):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.definir_ia_api_key('sk-teste')
        self.configuracao.save(update_fields=[
            'ia_ativa', 'ia_restringir_horario', 'ia_api_key_criptografada',
        ])
        conversa = self.nova_conversa('5584999996633')
        conversa.contexto = {
            'ia_agenda': {
                'fluxo_ativo': True,
                'servico_id': self.servico.pk,
                'servico_ids': [self.servico.pk],
                'complementos_confirmados': True,
                'profissional_id': self.profissional.pk,
            },
        }
        conversa.save(update_fields=['contexto'])
        responder.return_value = SimpleNamespace(conteudo={
            'acao': 'consultar_datas', 'mensagem': '',
            'servico_id': self.servico.pk,
            'servico_ids': [self.servico.pk],
            'confirmou_servicos': True,
            'profissional_id': self.profissional.pk,
            'aceita_qualquer_profissional': False,
            'aceita_qualquer_periodo': False,
            'data': '', 'periodo': '', 'horario': '', 'nome_cliente': '',
        })
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Pode olhar quando a agenda está livre para mim?',
        )

        resposta = processar_com_ia(conversa, mensagem.texto, mensagem)

        self.assertIn('encontrei estas próximas datas', resposta)
        responder.assert_called_once()

    @patch('apps.whatsapp_agent.intelligent_agent.CepService.consultar')
    @patch('apps.whatsapp_agent.intelligent_agent.OpenAIWhatsAppClient.responder_estruturado')
    def test_ia_conclui_pedido_inteiro_no_whatsapp_sem_enviar_link(self, responder, consultar_cep):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.definir_ia_api_key('sk-teste')
        self.configuracao.save(update_fields=[
            'ia_ativa', 'ia_restringir_horario', 'ia_api_key_criptografada',
        ])
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.servico.unidade_medida,
            descricao='Polpa de manga', ncm='00000000',
            tipo_produto=Produto.TipoProduto.UNITARIO,
            exibir_catalogo=True, catalogo_quantidade_maxima=10,
            preco_venda=Decimal('8.50'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        CatalogoConfiguracao.objects.create(
            filial=self.filial, ativo=True, entrega_ativa=True,
            retirada_ativa=True, frete_abaixo_limite='fixo',
            valor_frete=Decimal('6.00'),
        )
        consultar_cep.return_value = {
            'cep': '59000000', 'endereco': 'Rua das Flores',
            'complemento': '', 'bairro': 'Centro', 'cidade': 'Natal', 'uf': 'RN',
        }

        def decisao(acao='gerenciar_pedido', **extras):
            base = {
                'acao': acao, 'mensagem': '', 'servico_id': None,
                'servico_ids': [], 'confirmou_servicos': False,
                'profissional_id': None, 'aceita_qualquer_profissional': False,
                'aceita_qualquer_periodo': False, 'dias_semana': [],
                'data': '', 'periodo': '', 'horario': '', 'nome_cliente': '',
                'confirmou_nome': False, 'itens_pedido': [],
                'avancar_pedido': False, 'modalidade': '', 'cep': '',
                'logradouro': '', 'numero_endereco': '', 'complemento': '',
                'bairro': '', 'cidade': '', 'uf': '', 'forma_pagamento': '',
                'troco_para': '', 'observacao': '', 'sem_observacao': False,
                'cupom_codigo': '', 'data_entrega': '', 'hora_entrega': '',
                'confirmou_pedido': False,
            }
            base.update(extras)
            return SimpleNamespace(conteudo=base)

        responder.side_effect = [
            decisao('abrir_catalogo'),
            decisao(nome_cliente='Diego Cliente'),
            decisao(itens_pedido=[{
                'produto_id': produto.pk, 'quantidade': 2, 'operacao': 'adicionar',
            }]),
            decisao(modalidade='entrega'),
            decisao(cep='59000000'),
            decisao(numero_endereco='100'),
            decisao(forma_pagamento='pix'),
            decisao(sem_observacao=True),
        ]
        conversa = self.nova_conversa('5584999991212')

        respostas = []
        for texto in (
            'Quero fazer um pedido', 'Diego Cliente', 'Sim, está correto',
            'Quero duas polpas de manga',
            'Continuar', 'Entrega', '59000-000', 'Número 100', 'Pix',
            'Sem observação', 'Sim, confirmar',
        ):
            mensagem = MensagemWhatsApp.objects.create(
                conversa=conversa, direcao=MensagemWhatsApp.Direcao.ENTRADA, texto=texto,
            )
            respostas.append(processar_com_ia(conversa, texto, mensagem))
            conversa.refresh_from_db()

        self.assertIn('qual é o seu nome', respostas[0])
        self.assertIn('usando este WhatsApp', respostas[1])
        self.assertIn('Polpa de manga', respostas[2])
        self.assertNotIn('/catalogo/', '\n'.join(respostas))
        self.assertIn('Qual é o CEP', respostas[5])
        self.assertIn('Rua das Flores', respostas[6])
        self.assertIn('Confira seu pedido', respostas[9])
        self.assertIn('Polpa de manga', respostas[10])
        pedido = PedidoCatalogo.objects.get()
        self.assertEqual(pedido.status, PedidoCatalogo.Status.AGUARDANDO_LOJA)
        self.assertEqual(pedido.subtotal, Decimal('17.00'))
        self.assertEqual(pedido.valor_frete, Decimal('6.00'))
        self.assertEqual(pedido.total, Decimal('23.00'))
        self.assertEqual(pedido.endereco_entrega['numero'], '100')
        self.assertEqual(pedido.itens.get().quantidade, 2)
        self.assertTrue(Notificacao.objects.filter(referencia_id=str(pedido.pk)).exists())
        conversa.refresh_from_db()
        self.assertFalse(conversa.ativa)
        self.assertEqual(conversa.etapa, 'encerrada')

    @patch('apps.whatsapp_agent.intelligent_agent.OpenAIWhatsAppClient.responder_estruturado')
    def test_confirmacoes_naturais_avancam_o_carrinho_sem_depender_da_ia(self, responder):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.save(update_fields=['ia_ativa', 'ia_restringir_horario'])
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.servico.unidade_medida,
            descricao='Polpa de manga 100g', ncm='00000000',
            tipo_produto=Produto.TipoProduto.UNITARIO,
            exibir_catalogo=True, preco_venda=Decimal('6.00'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        confirmacoes = (
            'Continua', 'Continuar', 'Vai ser só os dois itens mesmo.',
            'Está certo', 'Isso mesmo', 'Pode seguir', 'Confirmo',
            'Não quero adicionar mais nada', 'Não', 'Fechou', 'Pode ir',
        )

        for indice, texto in enumerate(confirmacoes):
            with self.subTest(texto=texto):
                conversa = self.nova_conversa(f'558499998{indice:04d}')
                conversa.contexto = {
                    'ia_pedido': {
                        'fluxo_ativo': True,
                        'nome_cliente': 'Ana Cliente',
                        'nome_confirmado': True,
                        'itens': {str(produto.pk): 2},
                        'itens_confirmados': False,
                    },
                }
                conversa.etapa = 'ia_pedido'
                conversa.save(update_fields=['contexto', 'etapa'])
                mensagem = MensagemWhatsApp.objects.create(
                    conversa=conversa,
                    direcao=MensagemWhatsApp.Direcao.ENTRADA,
                    texto=texto,
                )

                resposta = processar_com_ia(conversa, texto, mensagem)

                self.assertIn('Como deseja receber', resposta)
                conversa.refresh_from_db()
                self.assertTrue(conversa.contexto['ia_pedido']['itens_confirmados'])

        responder.assert_not_called()

    @patch('apps.whatsapp_agent.intelligent_agent.OpenAIWhatsAppClient.responder_estruturado')
    def test_confirmacao_natural_finaliza_e_cria_o_pedido(self, responder):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.save(update_fields=['ia_ativa', 'ia_restringir_horario'])
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.servico.unidade_medida,
            descricao='Polpa de caju 100g', ncm='00000000',
            tipo_produto=Produto.TipoProduto.UNITARIO,
            exibir_catalogo=True, preco_venda=Decimal('7.00'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        conversa = self.nova_conversa('5584999971212')
        conversa.contexto = {
            'ia_pedido': {
                'fluxo_ativo': True,
                'nome_cliente': 'Ana Cliente', 'nome_confirmado': True,
                'itens': {str(produto.pk): 2}, 'itens_confirmados': True,
                'modalidade': 'retirada', 'forma_pagamento': 'pix',
                'observacao': '', 'observacao_respondida': True,
                'aguardando_confirmacao': True,
            },
        }
        conversa.etapa = 'ia_pedido'
        conversa.save(update_fields=['contexto', 'etapa'])
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa, direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Está tudo certo, pode finalizar.',
        )

        resposta = processar_com_ia(conversa, mensagem.texto, mensagem)

        self.assertIn('Polpa de caju 100g', resposta)
        pedido = PedidoCatalogo.objects.get()
        self.assertEqual(pedido.status, PedidoCatalogo.Status.AGUARDANDO_LOJA)
        self.assertEqual(pedido.total, Decimal('14.00'))
        responder.assert_not_called()

    @patch('apps.whatsapp_agent.intelligent_agent.OpenAIWhatsAppClient.responder_estruturado')
    def test_ia_inicia_agendamento_sem_enviar_link(self, responder):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.definir_ia_api_key('sk-teste')
        self.configuracao.save(update_fields=[
            'ia_ativa', 'ia_restringir_horario', 'ia_api_key_criptografada',
        ])
        responder.return_value = SimpleNamespace(conteudo={
            'acao': 'abrir_agenda', 'mensagem': '', 'servico_id': None,
            'servico_ids': [], 'confirmou_servicos': False,
            'profissional_id': None, 'aceita_qualquer_profissional': False,
            'aceita_qualquer_periodo': False, 'dias_semana': [],
            'data': '', 'periodo': '', 'horario': '', 'nome_cliente': '',
            'confirmou_nome': False, 'itens_pedido': [], 'avancar_pedido': False,
            'modalidade': '', 'cep': '', 'logradouro': '', 'numero_endereco': '',
            'complemento': '', 'bairro': '', 'cidade': '', 'uf': '',
            'forma_pagamento': '', 'troco_para': '', 'observacao': '',
            'sem_observacao': False, 'cupom_codigo': '', 'data_entrega': '',
            'hora_entrega': '', 'confirmou_pedido': False,
        })
        conversa = self.nova_conversa('5584999993434')
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa, direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Quero ver a agenda',
        )

        resposta = processar_com_ia(conversa, mensagem.texto, mensagem)

        self.assertEqual(resposta, 'Qual período você prefere: manhã, tarde ou noite?')
        self.assertNotIn('/agendar/', resposta)

    def test_saudacao_retoma_ia_apos_falha_tecnica(self):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.save(update_fields=['ia_ativa', 'ia_restringir_horario'])
        conversa = self.nova_conversa('5584999996688')
        conversa.atendimento_humano = True
        conversa.etapa = 'aguardando_atendimento_humano'
        conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.ATENDIMENTO_HUMANO
        conversa.save(update_fields=['atendimento_humano', 'etapa', 'etapa_crm'])
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Oi',
        )

        resposta = processar_com_ia(conversa, mensagem.texto, mensagem)

        self.assertEqual(resposta, 'Olá! Como posso ajudar você hoje?')
        conversa.refresh_from_db()
        self.assertFalse(conversa.atendimento_humano)
        self.assertEqual(conversa.etapa, 'ia_atendimento')

    @patch('apps.whatsapp_agent.intelligent_agent.OpenAIWhatsAppClient.responder_estruturado')
    def test_ia_interpreta_resposta_numerica_fora_do_menu(self, responder):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.definir_ia_api_key('sk-teste')
        self.configuracao.save(update_fields=[
            'ia_ativa', 'ia_restringir_horario', 'ia_api_key_criptografada',
        ])
        responder.return_value = SimpleNamespace(conteudo={
            'acao': 'responder',
            'mensagem': 'Entendi.',
            'servico_id': None,
            'profissional_id': None,
            'data': '',
            'periodo': '',
            'horario': '',
            'nome_cliente': '',
        })
        conversa = self.nova_conversa('5584999997777')
        conversa.etapa = 'ia_atendimento'
        conversa.save(update_fields=['etapa'])
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='2',
        )

        self.assertEqual(processar_com_ia(conversa, '2', mensagem), 'Entendi.')
        responder.assert_called_once()

    @patch('apps.whatsapp_agent.intelligent_agent.OpenAIWhatsAppClient.responder_estruturado')
    def test_ia_interpreta_pedido_de_menu_sem_abrir_menu_classico(self, responder):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.definir_ia_api_key('sk-teste')
        self.configuracao.save(update_fields=[
            'ia_ativa', 'ia_restringir_horario', 'ia_api_key_criptografada',
        ])
        responder.return_value = SimpleNamespace(conteudo={
            'acao': 'mostrar_menu',
            'mensagem': 'Posso ajudar com agendamento ou chamar um atendente. O que você precisa?',
            'servico_id': None,
            'profissional_id': None,
            'data': '',
            'periodo': '',
            'horario': '',
            'nome_cliente': '',
        })
        conversa = self.nova_conversa('5584999997788')
        conversa.etapa = 'aguardando_opcao'
        conversa.save(update_fields=['etapa'])
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='menu',
        )

        resposta = processar_com_ia(conversa, mensagem.texto, mensagem)

        self.assertNotIn('*1.*', resposta)
        self.assertIn('Posso ajudar', resposta)
        responder.assert_called_once()
        conversa.refresh_from_db()
        self.assertEqual(conversa.etapa, 'ia_atendimento')

    @patch('apps.whatsapp_agent.intelligent_agent.OpenAIWhatsAppClient.responder_estruturado')
    def test_falha_da_ia_avisa_e_encaminha_para_humano(self, responder):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.ia_mensagem_erro = 'Tive uma instabilidade. Nossa equipe vai continuar com você.'
        self.configuracao.definir_ia_api_key('sk-teste')
        self.configuracao.save(update_fields=[
            'ia_ativa', 'ia_restringir_horario', 'ia_mensagem_erro',
            'ia_api_key_criptografada',
        ])
        responder.side_effect = IAWhatsAppError('Falha simulada')
        conversa = self.nova_conversa('5584999997799')
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Vocês atendem crianças?',
        )

        resposta = processar_com_ia(conversa, mensagem.texto, mensagem)

        self.assertEqual(
            resposta,
            'Tive uma instabilidade. Nossa equipe vai continuar com você.',
        )
        conversa.refresh_from_db()
        self.assertTrue(conversa.atendimento_humano)
        self.assertEqual(
            conversa.etapa_crm,
            ConversaWhatsApp.EtapaCRM.ATENDIMENTO_HUMANO,
        )

    @patch('apps.whatsapp_agent.intelligent_agent._dentro_horario_ia', return_value=False)
    @patch('apps.whatsapp_agent.intelligent_agent.OpenAIWhatsAppClient.responder_estruturado')
    def test_fora_do_horario_avisa_e_encaminha_para_humano(
        self, responder, _dentro_horario,
    ):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = True
        self.configuracao.ia_horario_inicio = time(8)
        self.configuracao.ia_horario_fim = time(20)
        self.configuracao.save(update_fields=[
            'ia_ativa', 'ia_restringir_horario',
            'ia_horario_inicio', 'ia_horario_fim',
        ])
        conversa = self.nova_conversa('5584999998888')
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Quero comprar um produto',
        )

        resposta = processar_com_ia(conversa, mensagem.texto, mensagem)

        self.assertIn('das 08:00 às 20:00', resposta)
        responder.assert_not_called()
        conversa.refresh_from_db()
        self.assertTrue(conversa.atendimento_humano)
        self.assertEqual(conversa.etapa, 'aguardando_atendimento_humano')
        self.assertEqual(
            conversa.etapa_crm,
            ConversaWhatsApp.EtapaCRM.ATENDIMENTO_HUMANO,
        )

    def test_saudacao_exibe_menu_principal(self):
        conversa = self.nova_conversa()

        resposta = processar_mensagem(conversa, 'Olá')

        self.assertIn('*1.* Fazer um agendamento', resposta)
        self.assertIn('*2.* Falar com um atendente', resposta)
        self.assertIn('*3.* Encerrar a conversa', resposta)
        conversa.refresh_from_db()
        self.assertEqual(conversa.etapa, 'aguardando_opcao')

    def test_saudacao_nao_e_repetida_em_todo_oi(self):
        conversa = self.nova_conversa()

        primeira = processar_mensagem(conversa, 'oi')
        segunda = processar_mensagem(conversa, 'oi')

        self.assertIn(self.configuracao.mensagem_saudacao, primeira)
        self.assertNotIn(self.configuracao.mensagem_saudacao, segunda)
        self.assertIn('*1.* Fazer um agendamento', segunda)

    def test_modo_ambos_cria_menu_com_agenda_e_catalogo(self):
        self.configuracao.modo_atendimento = ConfiguracaoWhatsApp.ModoAtendimento.AMBOS
        self.configuracao.save(update_fields=['modo_atendimento'])
        self.configuracao.menus.all().delete()
        conversa = self.nova_conversa()

        resposta = processar_mensagem(conversa, 'oi')

        self.assertIn('Fazer um agendamento', resposta)
        self.assertIn('Fazer um pedido', resposta)
        self.assertIn('Falar com um atendente', resposta)

    def test_modo_catalogo_cria_menu_sem_agendamento(self):
        self.configuracao.modo_atendimento = ConfiguracaoWhatsApp.ModoAtendimento.CATALOGO
        self.configuracao.save(update_fields=['modo_atendimento'])
        self.configuracao.menus.all().delete()
        conversa = self.nova_conversa()

        resposta = processar_mensagem(conversa, 'oi')

        self.assertIn('Fazer um pedido', resposta)
        self.assertNotIn('Fazer um agendamento', resposta)

    def test_fluxo_personalizado_aceita_palavra_chave_e_submenu(self):
        conversa = self.nova_conversa()
        processar_mensagem(conversa, 'oi')
        principal = MenuWhatsApp.objects.get(configuracao=self.configuracao, principal=True)
        submenu = MenuWhatsApp.objects.create(
            filial=self.filial, configuracao=self.configuracao, codigo='duvidas',
            nome='Dúvidas', mensagem='Qual é a sua dúvida?',
        )
        OpcaoMenuWhatsApp.objects.create(
            menu=principal, chave='4', titulo='Tirar dúvidas',
            palavras_chave='ajuda, dúvidas', acao=OpcaoMenuWhatsApp.Acao.ABRIR_MENU,
            menu_destino=submenu, ordem=3,
        )
        OpcaoMenuWhatsApp.objects.create(
            menu=submenu, chave='1', titulo='Ver endereço',
            acao=OpcaoMenuWhatsApp.Acao.MENSAGEM, mensagem='Estamos na Rua Central, 10.',
        )

        resposta_submenu = processar_mensagem(conversa, 'ajuda')
        resposta_mensagem = processar_mensagem(conversa, '1')

        self.assertIn('Qual é a sua dúvida?', resposta_submenu)
        self.assertIn('*1.* Ver endereço', resposta_submenu)
        self.assertEqual(resposta_mensagem, 'Estamos na Rua Central, 10.')

    @override_settings(PUBLIC_BASE_URL='https://ited.app.br')
    def test_opcao_um_cria_e_envia_link_publico_da_agenda(self):
        conversa = self.nova_conversa()
        self.servico.agendavel = False
        self.servico.save(update_fields=['agendavel'])

        resposta = processar_mensagem(conversa, '1')

        link = AgendaLinkPublico.objects.get(filial=self.filial)
        self.assertIn(f'https://ited.app.br/agendar/{link.token}/', resposta)
        self.assertIn('?wa=', resposta)
        self.assertTrue(TenantPublicLink.objects.filter(tipo='agenda').exists())
        conversa.refresh_from_db()
        self.assertEqual(conversa.etapa_crm, ConversaWhatsApp.EtapaCRM.INTERESSADO)
        self.assertIn('link_agendamento', conversa.contexto)

    def test_opcao_dois_transfere_e_silencia_automacao(self):
        conversa = self.nova_conversa()

        resposta = processar_mensagem(conversa, '2')

        self.assertEqual(resposta, self.configuracao.mensagem_transferencia)
        conversa.refresh_from_db()
        self.assertTrue(conversa.atendimento_humano)
        self.assertEqual(conversa.etapa, 'atendimento_humano')
        self.assertEqual(
            conversa.etapa_crm,
            ConversaWhatsApp.EtapaCRM.ATENDIMENTO_HUMANO,
        )
        self.assertIsNone(processar_mensagem(conversa, 'oi'))

    @patch('apps.whatsapp_agent.agent._destino_agenda', return_value=(None, None))
    def test_opcao_um_nao_envia_link_quebrado_se_tenant_estiver_indisponivel(self, _destino):
        conversa = self.nova_conversa()

        resposta = processar_mensagem(conversa, '1')

        self.assertNotIn('/agendar/', resposta)
        self.assertIn('responda *2*', resposta)

    def test_opcao_tres_encerra_conversa(self):
        conversa = self.nova_conversa()

        resposta = processar_mensagem(conversa, '3')

        self.assertIn('Conversa encerrada', resposta)
        conversa.refresh_from_db()
        self.assertFalse(conversa.ativa)
        self.assertEqual(conversa.etapa, 'encerrada')
        self.assertEqual(conversa.etapa_crm, ConversaWhatsApp.EtapaCRM.NAO_CONVERTIDO)

    def test_encerramento_manual_marca_conversa_como_encerrada(self):
        conversa = self.nova_conversa()
        conversa.atendimento_humano = True
        conversa.save(update_fields=['atendimento_humano'])

        encerrar_conversa(conversa)

        conversa.refresh_from_db()
        self.assertFalse(conversa.ativa)
        self.assertFalse(conversa.atendimento_humano)
        self.assertEqual(conversa.etapa, 'encerrada')

    def test_encerramento_automatico_respeita_tempo_configurado(self):
        self.configuracao.encerramento_automatico_ativo = True
        self.configuracao.tempo_inatividade_minutos = 30
        self.configuracao.save(update_fields=[
            'encerramento_automatico_ativo', 'tempo_inatividade_minutos',
        ])
        antiga = self.nova_conversa('5584999990011')
        recente = self.nova_conversa('5584999990022')
        agora = timezone.now()
        ConversaWhatsApp.objects.filter(pk=antiga.pk).update(
            ultima_mensagem_em=agora - timedelta(minutes=31),
        )
        ConversaWhatsApp.objects.filter(pk=recente.pk).update(
            ultima_mensagem_em=agora - timedelta(minutes=29),
        )

        total = encerrar_conversas_inativas(agora=agora)

        antiga.refresh_from_db()
        recente.refresh_from_db()
        self.assertEqual(total, 1)
        self.assertFalse(antiga.ativa)
        self.assertEqual(antiga.etapa, 'encerrada')
        self.assertTrue(recente.ativa)

    @patch('apps.whatsapp_agent.webhook.EvolutionClient.enviar_texto')
    def test_nova_mensagem_cria_nova_sessao_apos_expiracao(self, enviar_texto):
        enviar_texto.return_value = {'key': {'id': 'saida-reaberta'}}
        self.configuracao.encerramento_automatico_ativo = True
        self.configuracao.tempo_inatividade_minutos = 10
        self.configuracao.save(update_fields=[
            'encerramento_automatico_ativo', 'tempo_inatividade_minutos',
        ])
        conversa = self.nova_conversa('5584999990033')
        ConversaWhatsApp.objects.filter(pk=conversa.pk).update(
            ultima_mensagem_em=timezone.now() - timedelta(minutes=11),
        )
        payload = {
            'event': 'messages.upsert', 'instance': self.configuracao.instancia,
            'data': {
                'key': {
                    'id': 'entrada-reaberta', 'fromMe': False,
                    'remoteJid': conversa.remote_jid,
                },
                'pushName': 'Cliente', 'message': {'conversation': 'Oi'},
            },
        }

        self.assertEqual(receber_evento(self.configuracao, payload), 'enviada')

        conversa.refresh_from_db()
        nova = ConversaWhatsApp.objects.exclude(pk=conversa.pk).get(
            telefone='5584999990033',
        )
        self.assertFalse(conversa.ativa)
        self.assertEqual(conversa.etapa, 'encerrada')
        self.assertTrue(nova.ativa)
        self.assertEqual(nova.mensagens.filter(direcao='entrada').count(), 1)
        self.assertEqual(conversa.mensagens.count(), 0)
        self.assertIn(self.configuracao.mensagem_saudacao, enviar_texto.call_args.args[1])

    @patch('apps.whatsapp_agent.webhook.EvolutionClient.enviar_texto')
    def test_nova_mensagem_cria_nova_sessao_apos_encerramento_manual(self, enviar_texto):
        enviar_texto.return_value = {'key': {'id': 'saida-nova-sessao'}}
        antiga = self.nova_conversa('5584999990043')
        MensagemWhatsApp.objects.create(
            conversa=antiga,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            texto='Mensagem da sessão anterior',
        )
        encerrar_conversa(antiga)
        payload = {
            'event': 'messages.upsert', 'instance': self.configuracao.instancia,
            'data': {
                'key': {
                    'id': 'entrada-nova-sessao', 'fromMe': False,
                    'remoteJid': antiga.remote_jid,
                },
                'pushName': 'Cliente', 'message': {'conversation': 'Oi'},
            },
        }

        self.assertEqual(receber_evento(self.configuracao, payload), 'enviada')

        nova = ConversaWhatsApp.objects.exclude(pk=antiga.pk).get(
            telefone='5584999990043',
        )
        antiga.refresh_from_db()
        self.assertFalse(antiga.ativa)
        self.assertEqual(
            list(antiga.mensagens.values_list('texto', flat=True)),
            ['Mensagem da sessão anterior'],
        )
        self.assertTrue(nova.ativa)
        self.assertEqual(nova.etapa_crm, ConversaWhatsApp.EtapaCRM.NOVA)

    def test_mensagem_enviada_pelo_atendente_renova_atividade(self):
        conversa = self.nova_conversa('5584999990044')
        momento_antigo = timezone.now() - timedelta(hours=2)
        ConversaWhatsApp.objects.filter(pk=conversa.pk).update(
            ultima_mensagem_em=momento_antigo,
        )
        payload = {
            'event': 'messages.upsert', 'instance': self.configuracao.instancia,
            'data': {
                'key': {
                    'id': 'saida-atendente', 'fromMe': True,
                    'remoteJid': conversa.remote_jid,
                },
                'message': {'conversation': 'Olá, estou atendendo você.'},
            },
        }

        self.assertEqual(receber_evento(self.configuracao, payload), 'ignorado')

        conversa.refresh_from_db()
        self.assertGreater(conversa.ultima_mensagem_em, momento_antigo)

    @patch('apps.whatsapp_agent.notifications.EvolutionClient.enviar_texto')
    def test_confirmacao_do_agendamento_e_enviada_e_registrada(self, enviar_texto):
        enviar_texto.return_value = {'key': {'id': 'confirmacao-1'}}
        self.configuracao.status = ConfiguracaoWhatsApp.Status.CONECTADO
        self.configuracao.mensagem_confirmacao_agendamento = (
            'Confirmado para {nome}: {servicos} com {profissional} '
            'em {data} às {horario}.'
        )
        self.configuracao.mensagem_lembrete_agendamento = (
            'Lembrete para {nome}: {servicos}, {data} às {horario}.'
        )
        self.configuracao.save(update_fields=[
            'status', 'mensagem_confirmacao_agendamento',
            'mensagem_lembrete_agendamento',
        ])
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
        self.assertIn('Confirmado para Ana Cliente', texto)
        self.assertIn('Corte de cabelo', texto)
        self.assertIn(str(self.profissional), texto)
        self.assertNotIn('{data}', texto)
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
        self.assertIn('Lembrete para Ana Cliente', enviar_texto.call_args.args[1])
        self.assertNotIn('{horario}', enviar_texto.call_args.args[1])
        self.assertTrue(MensagemWhatsApp.objects.filter(tipo='lembrete_agendamento').exists())
        conversa = ConversaWhatsApp.objects.get(telefone='5584999990000')
        self.assertEqual(conversa.etapa_crm, ConversaWhatsApp.EtapaCRM.AGENDADO)

    @patch('apps.whatsapp_agent.crm_service.EvolutionClient.enviar_texto')
    def test_recupera_uma_unica_vez_agendamento_iniciado_e_abandonado(self, enviar_texto):
        enviar_texto.return_value = {'key': {'id': 'recuperacao-1'}}
        self.configuracao.status = ConfiguracaoWhatsApp.Status.CONECTADO
        self.configuracao.recuperacao_agendamento_ativa = True
        self.configuracao.recuperacao_atraso_minutos = 30
        self.configuracao.recuperacao_horario_inicio = time(0, 0)
        self.configuracao.recuperacao_horario_fim = time(23, 59)
        self.configuracao.save(update_fields=[
            'status', 'recuperacao_agendamento_ativa',
            'recuperacao_atraso_minutos', 'recuperacao_horario_inicio',
            'recuperacao_horario_fim',
        ])
        conversa = self.nova_conversa('5584999990088')
        agora = timezone.now()
        conversa.nome_contato = 'Ana'
        conversa.etapa_crm = ConversaWhatsApp.EtapaCRM.AGENDAMENTO_INICIADO
        conversa.agendamento_iniciado_em = agora - timedelta(minutes=31)
        conversa.contexto = {'link_agendamento': 'https://ited.app.br/agendar/link-rastreado/'}
        conversa.save(update_fields=[
            'nome_contato', 'etapa_crm', 'agendamento_iniciado_em', 'contexto',
        ])

        primeiro_total = recuperar_agendamentos_abandonados(agora=agora)
        segundo_total = recuperar_agendamentos_abandonados(agora=agora + timedelta(minutes=1))

        self.assertEqual((primeiro_total, segundo_total), (1, 0))
        conversa.refresh_from_db()
        self.assertEqual(
            conversa.etapa_crm,
            ConversaWhatsApp.EtapaCRM.AGUARDANDO_CLIENTE,
        )
        self.assertIsNotNone(conversa.acompanhamento_enviado_em)
        texto = enviar_texto.call_args.args[1]
        self.assertIn('Ana', texto)
        self.assertIn('link-rastreado', texto)
        self.assertEqual(enviar_texto.call_count, 1)
        self.assertTrue(
            conversa.mensagens.filter(tipo='recuperacao_agendamento', status='enviada').exists()
        )

        pergunta = processar_mensagem(conversa, '3')
        self.assertIn('principal motivo', pergunta)
        conversa.refresh_from_db()
        self.assertEqual(conversa.etapa, 'aguardando_motivo_nao_agendamento')

        agradecimento = processar_mensagem(conversa, '1')
        self.assertIn('Obrigado', agradecimento)
        conversa.refresh_from_db()
        self.assertEqual(conversa.motivo_nao_agendamento, 'Não encontrou um horário')
        self.assertFalse(conversa.ativa)

    @patch('apps.whatsapp_agent.crm_service.EvolutionClient.enviar_texto')
    def test_recuperacao_nao_envia_para_agendado_ou_atendimento_humano(self, enviar_texto):
        self.configuracao.status = ConfiguracaoWhatsApp.Status.CONECTADO
        self.configuracao.recuperacao_agendamento_ativa = True
        self.configuracao.recuperacao_atraso_minutos = 5
        self.configuracao.recuperacao_horario_inicio = time(0, 0)
        self.configuracao.recuperacao_horario_fim = time(23, 59)
        self.configuracao.save(update_fields=[
            'status', 'recuperacao_agendamento_ativa',
            'recuperacao_atraso_minutos', 'recuperacao_horario_inicio',
            'recuperacao_horario_fim',
        ])
        agora = timezone.now()
        agendado = self.nova_conversa('5584999990066')
        humano = self.nova_conversa('5584999990077')
        ConversaWhatsApp.objects.filter(pk=agendado.pk).update(
            etapa_crm=ConversaWhatsApp.EtapaCRM.AGENDADO,
            agendamento_iniciado_em=agora - timedelta(minutes=10),
        )
        ConversaWhatsApp.objects.filter(pk=humano.pk).update(
            etapa_crm=ConversaWhatsApp.EtapaCRM.AGENDAMENTO_INICIADO,
            agendamento_iniciado_em=agora - timedelta(minutes=10),
            atendimento_humano=True,
        )

        total = recuperar_agendamentos_abandonados(agora=agora)

        self.assertEqual(total, 0)
        enviar_texto.assert_not_called()

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

    @patch('apps.whatsapp_agent.webhook.EvolutionClient.enviar_texto')
    def test_evento_repetido_nao_abre_nova_sessao_apos_encerramento(self, enviar_texto):
        enviar_texto.return_value = {'key': {'id': 'saida-evento-antigo'}}
        payload = {
            'event': 'messages.upsert', 'instance': self.configuracao.instancia,
            'data': {
                'key': {
                    'id': 'entrada-evento-antigo', 'fromMe': False,
                    'remoteJid': '5584999992233@s.whatsapp.net',
                },
                'pushName': 'Ana', 'message': {'conversation': 'Oi'},
            },
        }
        self.assertEqual(receber_evento(self.configuracao, payload), 'enviada')
        conversa = ConversaWhatsApp.objects.get(telefone='5584999992233')
        encerrar_conversa(conversa)

        self.assertEqual(receber_evento(self.configuracao, payload), 'duplicado')

        self.assertEqual(
            ConversaWhatsApp.objects.filter(telefone='5584999992233').count(),
            1,
        )
        enviar_texto.assert_called_once()

    @patch('apps.whatsapp_agent.webhook.processar_mensagem')
    @patch('apps.whatsapp_agent.webhook.processar_com_ia', return_value='Olá! Como posso ajudar?')
    @patch('apps.whatsapp_agent.webhook.EvolutionClient.enviar_texto')
    def test_webhook_aciona_ia_na_primeira_mensagem_de_texto(
        self, enviar_texto, processar_ia, processar_menu,
    ):
        self.configuracao.ia_ativa = True
        self.configuracao.ia_restringir_horario = False
        self.configuracao.save(update_fields=['ia_ativa', 'ia_restringir_horario'])
        enviar_texto.return_value = {'key': {'id': 'saida-ia'}}
        payload = {
            'event': 'messages.upsert',
            'instance': self.configuracao.instancia,
            'data': {
                'key': {
                    'id': 'entrada-ia',
                    'fromMe': False,
                    'remoteJid': '5584999993333@s.whatsapp.net',
                },
                'pushName': 'Ana',
                'message': {'conversation': 'Qual é o próximo horário disponível?'},
            },
        }

        self.assertEqual(receber_evento(self.configuracao, payload), 'enviada')

        entrada = MensagemWhatsApp.objects.get(identificador_externo='entrada-ia')
        processar_ia.assert_called_once_with(
            entrada.conversa,
            'Qual é o próximo horário disponível?',
            entrada,
        )
        processar_menu.assert_not_called()
        enviar_texto.assert_called_once_with(
            '5584999993333',
            'Olá! Como posso ajudar?',
        )


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
    def test_envia_pdf_como_documento_em_base64(self, requisicao):
        resposta = Mock(status_code=200)
        resposta.json.return_value = {'key': {'id': 'documento-1'}}
        requisicao.return_value = resposta

        retorno = EvolutionClient(self.configuracao()).enviar_documento(
            '5584999990000', b'%PDF-', 'pedido-123.pdf', 'Resumo do pedido 123',
        )

        self.assertEqual(retorno['key']['id'], 'documento-1')
        self.assertEqual(
            requisicao.call_args.args,
            ('POST', 'https://evolution.example.com/message/sendMedia/ited-teste'),
        )
        payload = requisicao.call_args.kwargs['json']
        self.assertEqual(payload['mediatype'], 'document')
        self.assertEqual(payload['mimetype'], 'application/pdf')
        self.assertEqual(payload['fileName'], 'pedido-123.pdf')
        self.assertEqual(payload['media'], 'JVBERi0=')

    @patch('apps.whatsapp_agent.gateway.requests.request')
    def test_envia_imagem_com_mimetype_e_legenda(self, requisicao):
        resposta = Mock(status_code=200)
        resposta.json.return_value = {'key': {'id': 'imagem-1'}}
        requisicao.return_value = resposta

        retorno = EvolutionClient(self.configuracao()).enviar_midia(
            '5584999990000', b'imagem', 'produto.png', 'image/png',
            tipo='imagem', legenda='Foto do produto',
        )

        self.assertEqual(retorno['key']['id'], 'imagem-1')
        payload = requisicao.call_args.kwargs['json']
        self.assertEqual(payload['mediatype'], 'image')
        self.assertEqual(payload['mimetype'], 'image/png')
        self.assertEqual(payload['caption'], 'Foto do produto')
        self.assertEqual(payload['media'], 'aW1hZ2Vt')

    @patch('apps.whatsapp_agent.gateway.requests.request')
    def test_baixa_audio_em_base64_pela_evolution(self, requisicao):
        resposta = Mock(status_code=200)
        resposta.json.return_value = {
            'base64': 'data:audio/ogg;base64,YXVkaW8=',
            'mimetype': 'audio/ogg',
        }
        requisicao.return_value = resposta

        retorno = EvolutionClient(self.configuracao()).obter_midia_base64({
            'key': {'id': 'audio-1'},
            'message': {'audioMessage': {'ptt': True}},
        })

        self.assertEqual(retorno['base64'], 'YXVkaW8=')
        self.assertEqual(retorno['mimetype'], 'audio/ogg')
        self.assertEqual(
            requisicao.call_args.args,
            ('POST', 'https://evolution.example.com/chat/getBase64FromMediaMessage/ited-teste'),
        )

    @patch('apps.whatsapp_agent.gateway.requests.request')
    def test_numero_inexistente_retorna_mensagem_amigavel(self, requisicao):
        resposta = Mock(status_code=400)
        resposta.json.return_value = {
            'response': {
                'message': [
                    {'jid': '558996234025@s.whatsapp.net', 'exists': False},
                ],
            },
        }
        requisicao.return_value = resposta

        with self.assertRaisesRegex(
            GatewayWhatsAppError,
            'O número informado não foi encontrado no WhatsApp',
        ):
            EvolutionClient(self.configuracao()).enviar_texto('558996234025', 'Olá')

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

    @patch('apps.whatsapp_agent.gateway.requests.request')
    def test_api_oficial_envia_template_aprovado(self, requisicao):
        resposta = Mock(status_code=200)
        resposta.json.return_value = {'messages': [{'id': 'wamid.meta-1'}]}
        requisicao.return_value = resposta
        configuracao = Mock(
            meta_phone_number_id='123456789',
            meta_business_account_id='987654321',
            meta_graph_version='v23.0',
        )
        configuracao.obter_meta_access_token.return_value = 'token-meta'

        retorno = MetaCloudClient(configuracao).enviar_template(
            '5584999990000',
            'aviso_vencimento_boleto',
            'pt_BR',
            ['Cliente', 'Empresa', 'DOC-1', 'R$ 10,00', '20/10/2026', 'https://boleto'],
        )

        self.assertEqual(retorno['messages'][0]['id'], 'wamid.meta-1')
        self.assertEqual(
            requisicao.call_args.args,
            ('POST', 'https://graph.facebook.com/v23.0/123456789/messages'),
        )
        self.assertEqual(
            requisicao.call_args.kwargs['headers']['Authorization'],
            'Bearer token-meta',
        )
        payload = requisicao.call_args.kwargs['json']
        self.assertEqual(payload['type'], 'template')
        self.assertEqual(payload['template']['name'], 'aviso_vencimento_boleto')
        self.assertEqual(len(payload['template']['components'][0]['parameters']), 6)

    def test_formulario_expoe_escolha_do_provedor_sem_revelar_segredos_salvos(self):
        campos = ConfiguracaoWhatsAppForm().fields

        self.assertIn('provedor', campos)
        self.assertIn('meta_phone_number_id', campos)
        self.assertIn('meta_access_token', campos)
        self.assertIn('meta_verify_token', campos)
        self.assertIn('meta_app_secret', campos)
        self.assertIn('cobranca_ativa', campos)
        self.assertIn('cobranca_intervalo_minutos', campos)
        self.assertNotIn('api_key_criptografada', campos)
        self.assertNotIn('meta_access_token_criptografado', campos)
        self.assertIsInstance(campos['meta_access_token'].widget, forms.PasswordInput)

    def test_fluxo_expoe_mensagens_da_agenda_para_personalizacao(self):
        campos = FluxoWhatsAppForm().fields

        self.assertIn('mensagem_confirmacao_agendamento', campos)
        self.assertIn('mensagem_lembrete_agendamento', campos)
        self.assertIn('mensagem_recuperacao_agendamento', campos)
        self.assertIn('mensagem_motivo_nao_agendamento', campos)
        self.assertIn('mensagem_feedback_agendamento', campos)
        self.assertIn('recuperacao_agendamento_ativa', campos)
        self.assertIn('pedido_status_notificados', campos)
        self.assertIn('mensagem_pedido_pronto_retirada', campos)
        self.assertEqual(
            campos['pedido_status_notificados'].label,
            'Enviar atualização nestas etapas',
        )
        self.assertIn('{nome}', campos['mensagem_confirmacao_agendamento'].initial)
        self.assertIn('{horario}', campos['mensagem_lembrete_agendamento'].initial)
        self.assertIn('ia_ativa', campos)
        self.assertIn('ia_restringir_horario', campos)
        self.assertIn('ia_horario_inicio', campos)
        self.assertIn('ia_horario_fim', campos)
        self.assertIn('ia_mensagem_fora_horario', campos)
        self.assertIn('ia_mensagem_erro', campos)
        self.assertIn('ia_audio_ativo', campos)
        self.assertIn('ia_api_key', campos)
        self.assertEqual(
            campos['ia_api_key'].widget.attrs['placeholder'],
            'Cole a nova chave da OpenAI',
        )

    def test_fluxo_mostra_estado_e_botao_quando_chave_ia_esta_configurada(self):
        html = render_to_string(
            'whatsapp_agent/_ia_api_key_field.html',
            {
                'form': FluxoWhatsAppForm(instance=ConfiguracaoWhatsApp()),
                'ia_api_key_configurada': True,
            },
        )

        self.assertIn('Chave configurada', html)
        self.assertIn('valor protegido', html)
        self.assertIn('Trocar chave', html)
        self.assertIn('Cancelar troca', html)
