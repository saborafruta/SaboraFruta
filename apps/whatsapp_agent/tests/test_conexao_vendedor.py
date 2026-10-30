import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Permissao, Usuario
from apps.whatsapp_agent.models import (
    ConexaoWhatsAppVendedor,
    ConversaWhatsApp,
    MensagemWhatsApp,
)


class ConexaoWhatsAppVendedorTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            razao_social='Empresa WhatsApp CRM', cnpj='44555666000113',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Matriz WhatsApp CRM',
            nome_fantasia='Matriz WhatsApp CRM', cnpj='44555666000113', uf='RN',
            is_matriz=True,
        )
        self.perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Vendedor WhatsApp', ativo=True,
        )
        Permissao.objects.create(
            perfil=self.perfil, modulo='crm', pode_ver=True,
            pode_criar=True, pode_editar=True,
        )
        self.vendedor = Usuario.objects.create_user(
            email='whatsapp@example.com', nome='Vendedor WhatsApp', password='teste1234',
            empresa=self.empresa, filial=self.filial, perfil=self.perfil,
        )
        perfil_gestor = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Gestor WhatsApp', is_admin=True, ativo=True,
        )
        self.gestor = Usuario.objects.create_user(
            email='gestor-whatsapp@example.com', nome='Gestor WhatsApp',
            password='teste1234', empresa=self.empresa, filial=self.filial,
            perfil=perfil_gestor,
        )

    def test_credenciais_meta_sao_armazenadas_criptografadas(self):
        conexao = ConexaoWhatsAppVendedor(
            empresa=self.empresa, filial_padrao=self.filial, vendedor=self.vendedor,
            meta_phone_number_id='123456789',
        )
        conexao.definir_meta_access_token('token-secreto')
        conexao.definir_meta_verify_token('verify-secreto')
        conexao.definir_meta_app_secret('app-secreto')
        conexao.full_clean()
        conexao.save()

        self.assertNotIn('token-secreto', conexao.meta_access_token_criptografado)
        self.assertEqual(conexao.obter_meta_access_token(), 'token-secreto')
        self.assertEqual(conexao.obter_meta_verify_token(), 'verify-secreto')
        self.assertEqual(conexao.obter_meta_app_secret(), 'app-secreto')
        self.assertTrue(conexao.somente_atendimento_humano)

    def test_gestor_cadastra_numero_e_segue_para_conexao_oficial(self):
        self.client.force_login(self.gestor)

        resposta = self.client.post(
            reverse('whatsapp_agent:crm-conexao-nova'),
            {
                'vendedor': self.vendedor.pk,
                'filial_padrao': self.filial.pk,
                'numero_conectado': '5584999994444',
                'nome_conectado': 'Vendas WhatsApp',
                'meta_graph_version': 'v23.0',
                'ativo': 'on',
            },
        )

        conexao = ConexaoWhatsAppVendedor.objects.get(
            numero_conectado='5584999994444',
        )
        self.assertRedirects(
            resposta,
            reverse(
                'whatsapp_agent:crm-conexao-onboarding',
                kwargs={'pk': conexao.pk},
            ),
        )
        self.assertEqual(conexao.status, ConexaoWhatsAppVendedor.Status.PENDENTE)
        self.assertTrue(conexao.somente_atendimento_humano)
        self.assertTrue(conexao.obter_meta_verify_token())

    def test_vendedor_visualiza_seu_numero_mas_nao_abre_cadastro(self):
        conexao = self._conexao_meta()
        self.client.force_login(self.vendedor)

        lista = self.client.get(reverse('whatsapp_agent:crm-conexoes'))
        cadastro = self.client.get(reverse('whatsapp_agent:crm-conexao-nova'))
        onboarding = self.client.get(
            reverse(
                'whatsapp_agent:crm-conexao-onboarding',
                kwargs={'pk': conexao.pk},
            ),
        )

        self.assertEqual(lista.status_code, 200)
        self.assertContains(lista, '5584999990000')
        self.assertRedirects(cadastro, reverse('whatsapp_agent:crm-conexoes'))
        self.assertEqual(onboarding.status_code, 200)
        self.assertContains(onboarding, 'Leia o QR Code da Meta')
        self.assertNotContains(onboarding, 'Verify token')

    def test_gestor_visualiza_dados_tecnicos_do_onboarding(self):
        conexao = self._conexao_meta()
        self.client.force_login(self.gestor)

        resposta = self.client.get(
            reverse(
                'whatsapp_agent:crm-conexao-onboarding',
                kwargs={'pk': conexao.pk},
            ),
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Dados técnicos do webhook')
        self.assertContains(resposta, 'verify-secreto')

    def test_conexao_recusa_vendedor_de_outra_empresa(self):
        outra = Empresa.objects.create(
            razao_social='Empresa Externa', cnpj='55666777000124',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        outra_filial = Filial.objects.create(
            empresa=outra, razao_social='Matriz Externa', nome_fantasia='Matriz Externa',
            cnpj='55666777000124', uf='RN', is_matriz=True,
        )
        outro_perfil = PerfilAcesso.objects.create(empresa=outra, nome='Vendedor', ativo=True)
        outro = Usuario.objects.create_user(
            email='externo@example.com', nome='Externo', password='teste1234',
            empresa=outra, filial=outra_filial, perfil=outro_perfil,
        )
        conexao = ConexaoWhatsAppVendedor(
            empresa=self.empresa, filial_padrao=self.filial, vendedor=outro,
        )
        with self.assertRaises(ValidationError):
            conexao.full_clean()

    def _conexao_meta(self):
        conexao = ConexaoWhatsAppVendedor(
            empresa=self.empresa,
            filial_padrao=self.filial,
            vendedor=self.vendedor,
            meta_phone_number_id='123456789',
            numero_conectado='5584999990000',
            status=ConexaoWhatsAppVendedor.Status.CONECTADO,
        )
        conexao.definir_meta_verify_token('verify-secreto')
        conexao.definir_meta_app_secret('app-secreto')
        conexao.definir_meta_access_token('access-secreto')
        conexao.save()
        return conexao

    def _post_meta(self, conexao, valor):
        corpo = json.dumps({
            'entry': [{'changes': [{'value': {
                'metadata': {'phone_number_id': conexao.meta_phone_number_id},
                **valor,
            }}]}],
        }).encode('utf-8')
        assinatura = 'sha256=' + hmac.new(
            b'app-secreto', corpo, hashlib.sha256,
        ).hexdigest()
        return self.client.post(
            reverse('whatsapp_agent:webhook', kwargs={'secret': conexao.webhook_secret}),
            data=corpo,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=assinatura,
        )

    def test_webhook_oficial_do_vendedor_valida_token_e_registra_entrada(self):
        conexao = self._conexao_meta()
        url = reverse(
            'whatsapp_agent:webhook', kwargs={'secret': conexao.webhook_secret},
        )

        verificacao = self.client.get(url, {
            'hub.mode': 'subscribe',
            'hub.verify_token': 'verify-secreto',
            'hub.challenge': 'desafio-vendedor',
        })
        evento = self._post_meta(conexao, {
            'contacts': [{
                'wa_id': '5584999991111',
                'profile': {'name': 'Cliente da carteira'},
            }],
            'messages': [{
                'id': 'wamid.entrada-1',
                'from': '5584999991111',
                'timestamp': '1793390400',
                'type': 'text',
                'text': {'body': 'Quero um orçamento'},
            }],
        })

        self.assertEqual(verificacao.status_code, 200)
        self.assertEqual(verificacao.content, b'desafio-vendedor')
        self.assertEqual(evento.status_code, 200)
        self.assertEqual(evento.json()['resultado'], 'recebido')
        conversa = ConversaWhatsApp.objects.get()
        self.assertEqual(conversa.conexao_vendedor, conexao)
        self.assertIsNone(conversa.configuracao)
        self.assertTrue(conversa.fora_carteira)
        self.assertTrue(conversa.janela_atendimento_ativa)
        mensagem = conversa.mensagens.get()
        self.assertEqual(mensagem.origem, MensagemWhatsApp.Origem.CLIENTE)
        self.assertEqual(mensagem.texto_original, 'Quero um orçamento')

    def test_eco_do_celular_e_exclusao_preservam_historico(self):
        conexao = self._conexao_meta()
        eco = self._post_meta(conexao, {
            'message_echoes': [{
                'id': 'wamid.saida-1',
                'from': '5584999990000',
                'to': '5584999991111',
                'type': 'text',
                'text': {'body': 'Segue a proposta'},
            }],
        })

        self.assertEqual(eco.status_code, 200)
        mensagem = MensagemWhatsApp.objects.get(identificador_externo='wamid.saida-1')
        self.assertEqual(mensagem.origem, MensagemWhatsApp.Origem.CELULAR)
        self.assertEqual(mensagem.autor_email, self.vendedor.email)
        self.assertEqual(mensagem.texto_original, 'Segue a proposta')

        exclusao = self._post_meta(conexao, {
            'message_echoes': [{
                'type': 'revoke',
                'message_id': 'wamid.saida-1',
            }],
        })
        self.assertEqual(exclusao.status_code, 200)
        mensagem.refresh_from_db()
        self.assertEqual(mensagem.status, 'apagada')
        self.assertIsNotNone(mensagem.apagada_em)
        self.assertEqual(mensagem.texto, 'Segue a proposta')
        self.assertEqual(mensagem.texto_original, 'Segue a proposta')

    @patch('apps.whatsapp_agent.views.MetaCloudClient.enviar_texto')
    def test_vendedor_envia_pelo_crm_e_mensagem_identifica_a_origem(
        self, enviar_texto,
    ):
        enviar_texto.return_value = {'messages': [{'id': 'wamid.crm-1'}]}
        conexao = self._conexao_meta()
        conversa = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao,
            filial=self.filial,
            remote_jid='5584999991111@s.whatsapp.net',
            telefone='5584999991111',
            nome_contato='Cliente CRM',
            fora_carteira=False,
            ultima_mensagem_cliente_em=timezone.now(),
        )
        self.client.force_login(self.vendedor)

        inbox = self.client.get(
            reverse('whatsapp_agent:crm-inbox-detail', kwargs={'pk': conversa.pk}),
        )

        resposta = self.client.post(
            reverse('whatsapp_agent:crm-enviar', kwargs={'pk': conversa.pk}),
            {'mensagem': 'Orçamento enviado pelo iTED'},
        )

        self.assertEqual(inbox.status_code, 200)
        self.assertContains(inbox, 'Cliente CRM')
        self.assertRedirects(
            resposta,
            reverse('whatsapp_agent:crm-inbox-detail', kwargs={'pk': conversa.pk}),
        )
        enviar_texto.assert_called_once_with(
            '5584999991111', 'Orçamento enviado pelo iTED',
        )
        mensagem = MensagemWhatsApp.objects.get()
        self.assertEqual(mensagem.origem, MensagemWhatsApp.Origem.CRM)
        self.assertEqual(mensagem.autor_email, self.vendedor.email)
        self.assertEqual(mensagem.identificador_externo, 'wamid.crm-1')

    @patch('apps.whatsapp_agent.gateway.MetaCloudClient.enviar_template')
    @patch('apps.whatsapp_agent.views.MetaCloudClient.enviar_texto')
    def test_crm_bloqueia_envio_apos_24_horas_sem_usar_modelo(
        self, enviar_texto, enviar_template,
    ):
        conexao = self._conexao_meta()
        conversa = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao,
            filial=self.filial,
            remote_jid='5584999993333@s.whatsapp.net',
            telefone='5584999993333',
            nome_contato='Cliente com janela encerrada',
            fora_carteira=False,
            ultima_mensagem_cliente_em=timezone.now() - timedelta(hours=25),
        )
        self.client.force_login(self.vendedor)

        pagina = self.client.get(
            reverse('whatsapp_agent:crm-inbox-detail', kwargs={'pk': conversa.pk}),
        )
        resposta = self.client.post(
            reverse('whatsapp_agent:crm-enviar', kwargs={'pk': conversa.pk}),
            {'mensagem': 'Esta mensagem não pode sair'},
        )

        self.assertEqual(pagina.status_code, 200)
        self.assertContains(pagina, 'Envio bloqueado após 24 horas')
        self.assertRedirects(
            resposta,
            reverse('whatsapp_agent:crm-inbox-detail', kwargs={'pk': conversa.pk}),
        )
        enviar_texto.assert_not_called()
        enviar_template.assert_not_called()
        self.assertFalse(MensagemWhatsApp.objects.exists())

    def test_vendedor_nao_acessa_conversa_fora_da_carteira(self):
        conexao = self._conexao_meta()
        conversa = ConversaWhatsApp.objects.create(
            conexao_vendedor=conexao,
            filial=self.filial,
            remote_jid='5584999992222@s.whatsapp.net',
            telefone='5584999992222',
            fora_carteira=True,
        )
        self.client.force_login(self.vendedor)

        resposta = self.client.get(
            reverse('whatsapp_agent:crm-inbox-detail', kwargs={'pk': conversa.pk}),
        )

        self.assertEqual(resposta.status_code, 404)

        self.client.force_login(self.gestor)
        resposta_gestor = self.client.get(
            reverse('whatsapp_agent:crm-inbox-detail', kwargs={'pk': conversa.pk}),
        )
        self.assertEqual(resposta_gestor.status_code, 200)
