import datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.forms.parametros import DestinatarioResumoWhatsAppForm
from apps.core.models import (
    DestinatarioResumoWhatsApp, Empresa, Filial, ParametrosSistema,
)
from apps.whatsapp_agent.models import ConfiguracaoWhatsAppCentral, EnvioResumoWhatsApp
from apps.whatsapp_agent.central_views import central_instancias
from apps.whatsapp_agent.resumo_service import (
    _metricas_filial, preparar_resumos_diarios, processar_proximo_resumo,
)


@override_settings(
    TENANT_DATABASE_ROUTING_ENABLED=False,
    WHATSAPP_EVOLUTION_URL='https://evolution.example.com',
    WHATSAPP_EVOLUTION_API_KEY='segredo',
)
class ResumoDiarioWhatsAppTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        empresa = Empresa.objects.create(
            razao_social='Empresa Resumo LTDA',
            nome_fantasia='Empresa Resumo',
            cnpj='81345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=empresa,
            razao_social='Filial Resumo',
            nome_fantasia='Loja Centro',
            cnpj='81345678000192',
            uf='CE',
        )
        cls.parametros = ParametrosSistema.objects.create(
            filial=cls.filial,
            resumo_whatsapp_ativo=True,
            resumo_whatsapp_incluir_agenda=True,
        )
        DestinatarioResumoWhatsApp.objects.create(
            parametros=cls.parametros,
            posicao=1,
            nome='Sócio 1',
            telefone='5584999990001',
        )
        DestinatarioResumoWhatsApp.objects.create(
            parametros=cls.parametros,
            posicao=2,
            nome='Sócio 2',
            telefone='5584999990002',
        )

    def setUp(self):
        self.configuracao = ConfiguracaoWhatsAppCentral.carregar()
        self.configuracao.resumos_ativos = True
        self.configuracao.status = self.configuracao.Status.CONECTADO
        self.configuracao.intervalo_entre_envios_minutos = 3
        self.configuracao.horario_inicio = datetime.time(0, 0)
        self.configuracao.horario_fim = datetime.time(23, 59)
        self.configuracao.save()

    def _metricas(self):
        return {
            'empresa': 'Empresa Resumo',
            'filial': 'Loja Centro',
            'data': '07/10/2026',
            'vendas': 'R$ 1.000,00',
            'recebido': 'R$ 900,00',
            'quantidade_vendas': 10,
            'ticket_medio': 'R$ 100,00',
            'descontos': 'R$ 20,00',
            'cancelamentos': 1,
            'estoque_critico': '2 produto(s)',
            'vencimentos': '1 produto(s)',
            'contas_vencidas': '1 conta(s) — R$ 50,00',
            'comparacao': '+10,0%',
            'agenda': '\n\n📅 *Agenda:* 3 atendimento(s)',
        }

    @patch('apps.whatsapp_agent.resumo_service._metricas_filial')
    def test_prepara_fila_individual_e_idempotente(self, metricas_mock):
        metricas_mock.return_value = self._metricas()
        referencia = timezone.localdate() - datetime.timedelta(days=1)

        primeiro = preparar_resumos_diarios(referencia)
        segundo = preparar_resumos_diarios(referencia)

        self.assertEqual((primeiro, segundo), (2, 0))
        envios = list(EnvioResumoWhatsApp.objects.order_by('agendado_para'))
        self.assertEqual([item.telefone for item in envios], [
            '5584999990001', '5584999990002',
        ])
        self.assertGreaterEqual(
            envios[1].agendado_para - envios[0].agendado_para,
            datetime.timedelta(minutes=2, seconds=15),
        )
        metricas_mock.assert_called_with(self.filial, referencia, True)
        self.assertIn('Loja Centro', envios[0].mensagem)
        self.assertEqual(envios[0].tenant_alias, 'default')

    @patch('apps.whatsapp_agent.resumo_service.EvolutionClient.enviar_texto')
    def test_processador_envia_uma_por_intervalo(self, enviar_mock):
        enviar_mock.return_value = {'key': {'id': 'mensagem-1'}}
        agora = timezone.now() - datetime.timedelta(minutes=10)
        for indice in range(2):
            EnvioResumoWhatsApp.objects.create(
                configuracao=self.configuracao,
                empresa_nome='Empresa Resumo',
                filial_nome='Loja Centro',
                filial_cnpj=self.filial.cnpj,
                destinatario_nome=f'Sócio {indice + 1}',
                telefone=f'558499999000{indice + 1}',
                data_referencia=timezone.localdate() - datetime.timedelta(days=1),
                metricas=self._metricas(),
                mensagem='Resumo diário',
                agendado_para=agora,
            )

        self.assertEqual(processar_proximo_resumo(), 1)
        self.assertEqual(processar_proximo_resumo(), 0)
        self.assertEqual(
            EnvioResumoWhatsApp.objects.filter(status=EnvioResumoWhatsApp.Status.ENVIADO).count(),
            1,
        )
        self.configuracao.refresh_from_db()
        self.configuracao.ultimo_envio_em = timezone.now() - datetime.timedelta(minutes=4)
        self.configuracao.save(update_fields=['ultimo_envio_em'])
        self.assertEqual(processar_proximo_resumo(), 1)
        self.assertEqual(enviar_mock.call_count, 2)

    @patch('apps.whatsapp_agent.resumo_service.EvolutionClient.enviar_texto')
    @patch('apps.whatsapp_agent.resumo_service._metricas_filial')
    def test_disparo_manual_funciona_fora_da_janela_e_com_automatico_desligado(
        self, metricas_mock, enviar_mock,
    ):
        metricas_mock.return_value = self._metricas()
        enviar_mock.return_value = {'key': {'id': 'manual-1'}}
        self.configuracao.resumos_ativos = False
        self.configuracao.horario_inicio = datetime.time(0, 0)
        self.configuracao.horario_fim = datetime.time(0, 1)
        self.configuracao.save()

        criados = preparar_resumos_diarios(disparo_manual=True)

        self.assertEqual(criados, 2)
        primeiro = EnvioResumoWhatsApp.objects.order_by('agendado_para').first()
        self.assertTrue(primeiro.disparo_manual)
        self.assertLessEqual(primeiro.agendado_para, timezone.now())
        self.assertEqual(processar_proximo_resumo(), 1)
        enviar_mock.assert_called_once()

    def test_formulario_normaliza_numero_brasileiro(self):
        form = DestinatarioResumoWhatsAppForm(data={
            'posicao': 1,
            'nome': 'Sócio',
            'telefone': '(84) 99999-0000',
            'ativo': True,
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['telefone'], '5584999990000')

    def test_metricas_vazias_sao_geradas_sem_exigir_agenda(self):
        metricas = _metricas_filial(
            self.filial,
            timezone.localdate() - datetime.timedelta(days=1),
            incluir_agenda=False,
        )
        self.assertEqual(metricas['quantidade_vendas'], 0)
        self.assertEqual(metricas['agenda'], '')
        self.assertEqual(metricas['filial'], 'Loja Centro')

    @patch('apps.whatsapp_agent.central_views.render', return_value=HttpResponse('ok'))
    def test_central_exibe_tambem_filial_sem_instancia_configurada(self, render_mock):
        request = RequestFactory().get('/gestao/central/whatsapp/')
        request.user = SimpleNamespace(is_authenticated=True, is_superuser=True)

        resposta = central_instancias(request)

        self.assertEqual(resposta.status_code, 200)
        contexto = render_mock.call_args.args[2]
        self.assertEqual(len(contexto['linhas']), 1)
        self.assertEqual(contexto['total_filiais'], 1)
        self.assertIsNone(contexto['linhas'][0]['configuracao'])
