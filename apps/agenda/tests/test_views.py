from datetime import datetime
from unittest.mock import patch

from django.http import HttpResponse
from django.template.loader import get_template
from django.test import RequestFactory, override_settings
from django.utils import timezone

from apps.agenda.models import (
    AgendaLinkPublico,
    Agendamento,
    BloqueioAgenda,
    JornadaTrabalho,
    ProfissionalAgenda,
)
from apps.agenda.services import criar_agendamento
from apps.agenda.views import (
    AgendaView,
    AgendamentoCreateView,
    AgendamentoStatusView,
    BloqueioDeleteView,
    ProfissionalConfigView,
)
from apps.cadastros.models import Funcionario

from .test_disponibilidade import DisponibilidadeAgendaTests


class AgendaViewsTests(DisponibilidadeAgendaTests):
    def setUp(self):
        self.factory = RequestFactory()

    def preparar_request(self, request, session=None):
        request.filial_ativa = self.filial
        request.user = None
        request.session = session if session is not None else {}
        return request

    @patch('apps.agenda.views.messages.success')
    def test_salva_configuracao_do_profissional_com_jornada_e_intervalo(self, _success):
        funcionario = Funcionario.objects.create(filial=self.filial, nome='Ana Agenda')
        request = self.preparar_request(self.factory.post('/agenda/profissionais/novo/', {
            'funcionario': funcionario.pk,
            'servicos': [self.corte.pk, self.barba.pk],
            'intervalo_padrao_minutos': 10,
            'cor': '#7c3aed',
            'ativo': 'on',
            'dia_0_ativo': 'on',
            'dia_0_inicio': '08:00',
            'dia_0_fim': '18:00',
            'dia_0_pausa_inicio': '12:00',
            'dia_0_pausa_fim': '13:00',
        }))

        response = ProfissionalConfigView().post(request)

        self.assertEqual(response.status_code, 302)
        profissional = ProfissionalAgenda.objects.get(funcionario=funcionario)
        self.assertEqual(profissional.cor, '#7c3aed')
        self.assertEqual(profissional.servicos_vinculados.filter(ativo=True).count(), 2)
        jornada = JornadaTrabalho.objects.get(profissional=profissional, dia_semana=0)
        self.assertEqual(jornada.inicio.strftime('%H:%M'), '08:00')
        self.assertEqual(jornada.pausa_inicio.strftime('%H:%M'), '12:00')

    def test_formulario_exibe_horario_padrao_e_ajuste_por_dia(self):
        fonte = get_template('agenda/profissional_form.html').template.source

        self.assertIn('id="horario-padrao-inicio"', fonte)
        self.assertIn('id="horario-padrao-fim"', fonte)
        self.assertIn('id="horario-padrao-tem-intervalo"', fonte)
        self.assertIn('Ajustar este dia', fonte)
        self.assertIn('Usar horário padrão', fonte)

    @patch('apps.agenda.views.LogSistema.objects.create')
    @patch('apps.agenda.views.messages.success')
    def test_cria_agendamento_manual_pelo_post_correto(self, _success, _log):
        inicio = timezone.make_aware(datetime.combine(self.data_teste, self.profissional.jornadas.get(dia_semana=0).inicio))
        request = self.preparar_request(self.factory.post('/agenda/novo/', {
            'profissional': self.profissional.pk,
            'servicos': [self.corte.pk],
            'pessoa_atendida_nome': 'Cliente Manual',
            'telefone_contato': '5584999999999',
            'inicio': timezone.localtime(inicio).strftime('%Y-%m-%dT%H:%M'),
            'observacao': '',
        }))

        response = AgendamentoCreateView().post(request)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Agendamento.objects.filter(pessoa_atendida_nome='Cliente Manual').exists())

    @patch('apps.agenda.views.LogSistema.objects.create')
    @patch('apps.agenda.views.messages.success')
    def test_atualiza_status_sem_confundir_com_criacao(self, _success, _log):
        agendamento = criar_agendamento(
            filial=self.filial, profissional=self.profissional, servicos=[self.corte],
            inicio=self.inicio(8), pessoa_atendida_nome='Cliente Status',
        )
        request = self.preparar_request(self.factory.post(
            f'/agenda/agendamentos/{agendamento.pk}/status/',
            {'status': Agendamento.Status.CANCELADO},
        ))

        response = AgendamentoStatusView().post(request, agendamento.pk)

        self.assertEqual(response.status_code, 302)
        agendamento.refresh_from_db()
        self.assertEqual(agendamento.status, Agendamento.Status.CANCELADO)

    @patch('apps.agenda.views.messages.success')
    def test_remove_bloqueio_sem_confundir_com_profissional(self, _success):
        bloqueio = BloqueioAgenda.objects.create(
            filial=self.filial, inicio=self.inicio(8), fim=self.inicio(9), motivo='Teste',
        )
        request = self.preparar_request(self.factory.post(f'/agenda/bloqueios/{bloqueio.pk}/remover/'))

        response = BloqueioDeleteView().post(request, bloqueio.pk)

        self.assertEqual(response.status_code, 302)
        bloqueio.refresh_from_db()
        self.assertFalse(bloqueio.ativo)

    @patch('apps.agenda.views.render')
    def test_agenda_abre_semana_com_sete_colunas_e_troca_para_mes(self, render):
        render.return_value = HttpResponse()
        request = self.preparar_request(self.factory.get('/agenda/', {
            'visualizacao': 'semana', 'data': self.data_teste.isoformat(),
        }))

        AgendaView().get(request)
        contexto_semana = render.call_args.args[2]

        self.assertEqual(contexto_semana['visualizacao'], 'semana')
        self.assertEqual(len(contexto_semana['dias_grade']), 7)

        request = self.preparar_request(self.factory.get('/agenda/', {
            'visualizacao': 'mes', 'data': self.data_teste.isoformat(),
        }))
        AgendaView().get(request)
        contexto_mes = render.call_args.args[2]

        self.assertEqual(contexto_mes['visualizacao'], 'mes')
        self.assertIn(' de ', contexto_mes['titulo_periodo'])
        self.assertNotIn('October', contexto_mes['titulo_periodo'])
        self.assertTrue(contexto_mes['semanas_mes'])
        self.assertTrue(all(len(semana) == 7 for semana in contexto_mes['semanas_mes']))

    @patch('apps.agenda.views.render')
    def test_faixa_de_horarios_e_salva_na_sessao(self, render):
        render.return_value = HttpResponse()
        session = {}
        request = self.preparar_request(self.factory.get('/agenda/', {
            'visualizacao': 'dia',
            'data': self.data_teste.isoformat(),
            'hora_inicio': '10',
            'hora_fim': '16',
        }), session=session)

        AgendaView().get(request)
        contexto = render.call_args.args[2]

        self.assertEqual((contexto['hora_inicio'], contexto['hora_fim']), (10, 16))
        self.assertEqual(contexto['altura_grade'], 6 * 64)
        self.assertEqual(contexto['horas_grade'][0]['rotulo'], '10:00')
        self.assertEqual(contexto['horas_grade'][-1]['rotulo'], '16:00')

        request = self.preparar_request(self.factory.get('/agenda/', {
            'visualizacao': 'semana',
            'data': self.data_teste.isoformat(),
        }), session=session)
        AgendaView().get(request)
        contexto_persistido = render.call_args.args[2]

        self.assertEqual(
            (contexto_persistido['hora_inicio'], contexto_persistido['hora_fim']),
            (10, 16),
        )

    @patch('apps.agenda.views.TenantPublicLinkService.register')
    @patch('apps.agenda.views.messages.success')
    def test_gera_link_publico_da_filial(self, _success, register):
        from apps.agenda.views import AgendaLinkPublicoView

        request = self.preparar_request(self.factory.post('/agenda/link-publico/'))
        request._get_scheme = lambda: 'https'
        request.META['HTTP_HOST'] = 'ited.app.br'

        response = AgendaLinkPublicoView().post(request)

        self.assertEqual(response.status_code, 302)
        link = AgendaLinkPublico.objects.get(filial=self.filial)
        register.assert_called_once_with(kind='agenda', token=link.token, db_alias='default')

    @override_settings(ALLOWED_HOSTS=['ited.app.br'])
    @patch('apps.agenda.views.render')
    def test_agenda_exibe_link_publico_ja_gerado(self, render):
        render.return_value = HttpResponse()
        link = AgendaLinkPublico.objects.create(filial=self.filial)
        request = self.preparar_request(self.factory.get('/agenda/'))
        request._get_scheme = lambda: 'https'
        request.META['HTTP_HOST'] = 'ited.app.br'

        AgendaView().get(request)

        contexto = render.call_args.args[2]
        self.assertEqual(
            contexto['agenda_link_publico'],
            f'https://ited.app.br/agendar/{link.token}/',
        )
