import hashlib
import hmac
import json
from datetime import date, time
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.financeiro.constants.enums import TipoFormaPagamento
from apps.financeiro.models import ContaReceber, FormaPagamento
from apps.whatsapp_agent.cobranca_service import processar_proxima_cobranca
from apps.whatsapp_agent.models import (
    ConfiguracaoWhatsApp,
    ConsentimentoCobrancaWhatsApp,
    EnvioCobrancaWhatsApp,
)


class CobrancaBoletoWhatsAppTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Cobrança',
            nome_fantasia='Empresa Cobrança',
            cnpj='44111222000155',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Empresa Cobrança',
            nome_fantasia='Matriz',
            cnpj='44111222000155',
            uf='RN',
            cidade='Natal',
            cep='59000000',
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial,
            tipo_pessoa='F',
            razao_social='Cliente Avisado',
            cpf_cnpj='12345678901',
            celular='84999990000',
        )
        cls.forma = FormaPagamento.objects.create(
            empresa=cls.empresa,
            filial=cls.filial,
            descricao='Boleto',
            tipo=TipoFormaPagamento.BOLETO,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Administrador',
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='cobranca@example.test',
            nome='Operador',
            password='teste',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=perfil,
        )

    def setUp(self):
        self.configuracao = ConfiguracaoWhatsApp.objects.create(
            filial=self.filial,
            instancia='cobranca-teste',
            status=ConfiguracaoWhatsApp.Status.CONECTADO,
            ativo=True,
            cobranca_ativa=True,
            cobranca_horario_inicio=time(0, 0),
            cobranca_horario_fim=time(23, 59),
            cobranca_intervalo_minutos=1,
        )
        self.titulo = ContaReceber.objects.create(
            filial=self.filial,
            cliente=self.cliente,
            documento_numero='BOL-100',
            valor_original=Decimal('149.90'),
            valor_final=Decimal('149.90'),
            valor_saldo=Decimal('149.90'),
            data_emissao=date(2026, 10, 20),
            data_vencimento=date(2026, 10, 25),
            forma_pagamento=self.forma,
        )
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

    def _agendar_pela_tela(self):
        return self.client.post(reverse('financeiro:boleto_whatsapp_agendar'), {
            'titulos': [self.titulo.pk],
            'confirmar_consentimento': 'on',
        })

    def test_tela_registra_consentimento_e_coloca_aviso_na_fila_uma_vez(self):
        resposta = self._agendar_pela_tela()

        self.assertEqual(resposta.status_code, 302)
        self.assertIn('aba=whatsapp', resposta.url)
        self.assertTrue(ConsentimentoCobrancaWhatsApp.objects.filter(
            cliente_id=self.cliente.pk,
            telefone='5584999990000',
            autorizado=True,
        ).exists())
        envio = EnvioCobrancaWhatsApp.objects.get()
        self.assertEqual(envio.status, EnvioCobrancaWhatsApp.Status.PENDENTE)
        self.assertEqual(envio.documento, 'BOL-100')
        self.assertEqual(envio.valor, Decimal('149.90'))

        self._agendar_pela_tela()
        self.assertEqual(EnvioCobrancaWhatsApp.objects.count(), 1)

    @patch('apps.whatsapp_agent.cobranca_service.cliente_whatsapp')
    def test_evolution_envia_texto_respeitando_a_fila(self, criar_cliente):
        self._agendar_pela_tela()
        EnvioCobrancaWhatsApp.objects.update(agendado_para=timezone.now())
        cliente = Mock()
        cliente.enviar_texto.return_value = {'key': {'id': 'evolution-1'}}
        criar_cliente.return_value = cliente

        total = processar_proxima_cobranca()

        self.assertEqual(total, 1)
        envio = EnvioCobrancaWhatsApp.objects.get()
        self.assertEqual(envio.status, EnvioCobrancaWhatsApp.Status.ENVIADO)
        self.assertEqual(envio.identificador_externo, 'evolution-1')
        cliente.enviar_texto.assert_called_once_with(envio.telefone, envio.mensagem)
        cliente.enviar_template.assert_not_called()

    @patch('apps.whatsapp_agent.cobranca_service.cliente_whatsapp')
    def test_api_oficial_envia_template_aprovado(self, criar_cliente):
        self.configuracao.provedor = ConfiguracaoWhatsApp.Provedor.META_CLOUD
        self.configuracao.meta_phone_number_id = '123456789'
        self.configuracao.cobranca_template_meta = 'aviso_vencimento_boleto'
        self.configuracao.save()
        self._agendar_pela_tela()
        EnvioCobrancaWhatsApp.objects.update(agendado_para=timezone.now())
        cliente = Mock()
        cliente.enviar_template.return_value = {'messages': [{'id': 'wamid.meta-1'}]}
        criar_cliente.return_value = cliente

        total = processar_proxima_cobranca()

        self.assertEqual(total, 1)
        envio = EnvioCobrancaWhatsApp.objects.get()
        cliente.enviar_template.assert_called_once_with(
            envio.telefone,
            'aviso_vencimento_boleto',
            'pt_BR',
            envio.parametros_template,
        )
        cliente.enviar_texto.assert_not_called()
        self.assertEqual(envio.identificador_externo, 'wamid.meta-1')

    def test_api_oficial_nao_agenda_sem_consentimento_registrado(self):
        self.configuracao.provedor = ConfiguracaoWhatsApp.Provedor.META_CLOUD
        self.configuracao.cobranca_exigir_consentimento = False
        self.configuracao.save(update_fields=['provedor', 'cobranca_exigir_consentimento'])

        resposta = self.client.post(reverse('financeiro:boleto_whatsapp_agendar'), {
            'titulos': [self.titulo.pk],
        })

        self.assertEqual(resposta.status_code, 302)
        self.assertFalse(EnvioCobrancaWhatsApp.objects.exists())

    def test_webhook_oficial_valida_token_e_assinatura(self):
        self.configuracao.provedor = ConfiguracaoWhatsApp.Provedor.META_CLOUD
        self.configuracao.meta_phone_number_id = '123456789'
        self.configuracao.definir_meta_verify_token('verify-secret')
        self.configuracao.definir_meta_app_secret('app-secret')
        self.configuracao.save()
        url = reverse('whatsapp_agent:webhook', kwargs={
            'secret': self.configuracao.webhook_secret,
        })

        verificacao = self.client.get(url, {
            'hub.mode': 'subscribe',
            'hub.verify_token': 'verify-secret',
            'hub.challenge': 'desafio-123',
        })
        self.assertEqual(verificacao.status_code, 200)
        self.assertEqual(verificacao.content, b'desafio-123')

        corpo = json.dumps({'entry': []}).encode('utf-8')
        assinatura = 'sha256=' + hmac.new(
            b'app-secret', corpo, hashlib.sha256,
        ).hexdigest()
        evento = self.client.post(
            url,
            data=corpo,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=assinatura,
        )
        self.assertEqual(evento.status_code, 200)
        self.assertEqual(evento.json()['resultado'], 'evento_registrado')

    @patch('apps.whatsapp_agent.cobranca_service.cliente_whatsapp')
    def test_titulo_quitado_nao_e_enviado(self, criar_cliente):
        self._agendar_pela_tela()
        EnvioCobrancaWhatsApp.objects.update(agendado_para=timezone.now())
        self.titulo.status = 'pago'
        self.titulo.valor_saldo = Decimal('0')
        self.titulo.save(update_fields=['status', 'valor_saldo'])

        total = processar_proxima_cobranca()

        self.assertEqual(total, 0)
        self.assertEqual(
            EnvioCobrancaWhatsApp.objects.get().status,
            EnvioCobrancaWhatsApp.Status.CANCELADO,
        )
        criar_cliente.assert_not_called()
