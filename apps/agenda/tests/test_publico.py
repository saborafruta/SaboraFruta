import json
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import reverse

from apps.agenda.models import AgendaLinkPublico, Agendamento
from apps.agenda.views_publico import AgendaPublicaView, HorariosPublicosView
from apps.cadastros.models import Cliente

from .test_disponibilidade import DisponibilidadeAgendaTests


class AgendaPublicaTests(DisponibilidadeAgendaTests):
    def setUp(self):
        self.factory = RequestFactory()
        self.link = AgendaLinkPublico.objects.create(filial=self.filial)

    @staticmethod
    def anonimo(request):
        request.user = AnonymousUser()
        return request

    def test_pagina_publica_abre_sem_login(self):
        response = self.client.get(reverse('agenda_publica:agendar', args=[self.link.token]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Escolha o melhor horário')
        self.assertContains(response, self.profissional.funcionario.nome)
        self.assertContains(response, self.corte.descricao)
        self.assertContains(response, 'class="professional-avatar"')

    def test_foto_do_funcionario_aparece_na_escolha_publica(self):
        funcionario = self.profissional.funcionario
        funcionario.foto = 'funcionarios/fotos/carlos.jpg'
        funcionario.save(update_fields=['foto', 'updated_at'])

        response = self.client.get(reverse('agenda_publica:agendar', args=[self.link.token]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/media/funcionarios/fotos/carlos.jpg')
        self.assertContains(response, f'Foto de {funcionario.nome}')

    def test_logo_da_filial_aparece_na_agenda_publica(self):
        self.filial.imagem = 'filiais/imagens/logo-matriz.png'
        self.filial.save(update_fields=['imagem', 'updated_at'])

        response = self.client.get(reverse('agenda_publica:agendar', args=[self.link.token]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/media/filiais/imagens/logo-matriz.png')
        self.assertContains(response, f'Logo de {self.filial.nome_fantasia}')
        self.assertNotContains(response, 'class="brand-mark"')

    def test_api_retorna_apenas_horarios_disponiveis(self):
        request = self.anonimo(self.factory.get(f'/agendar/{self.link.token}/horarios/', {
            'profissional': self.profissional.pk,
            'servico': self.corte.pk,
            'data': self.data_teste.isoformat(),
        }))

        response = HorariosPublicosView().get(request, self.link.token)

        self.assertEqual(response.status_code, 200)
        valores = [item['valor'] for item in json.loads(response.content)['horarios']]
        self.assertIn('08:00', valores)
        self.assertNotIn('10:00', valores)

    @patch('apps.agenda.views_publico.enviar_notificacao_agendamento')
    def test_cliente_confirma_entra_na_agenda_e_recebe_whatsapp(self, enviar_notificacao):
        request = self.anonimo(self.factory.post(f'/agendar/{self.link.token}/', {
            'profissional': self.profissional.pk,
            'servico': self.corte.pk,
            'data': self.data_teste.isoformat(),
            'horario': '08:00',
            'nome': 'Cliente do Link',
            'telefone': '(84) 99999-0000',
        }))

        response = AgendaPublicaView().post(request, self.link.token)

        self.assertEqual(response.status_code, 200)
        cliente = Cliente.objects.get(celular='84999990000')
        agendamento = Agendamento.objects.get(cliente=cliente)
        self.assertEqual(agendamento.origem, Agendamento.Origem.LINK)
        self.assertEqual(agendamento.profissional, self.profissional)
        self.assertContains(response, 'Agendamento confirmado!')
        enviar_notificacao.assert_called_once_with(agendamento, db_alias='default')

    def test_horario_duplicado_e_rejeitado_sem_criar_outro_cliente(self):
        dados = {
            'profissional': self.profissional.pk,
            'servico': self.corte.pk,
            'data': self.data_teste.isoformat(),
            'horario': '08:00',
            'nome': 'Primeiro Cliente',
            'telefone': '84999990001',
        }
        primeira = AgendaPublicaView().post(
            self.anonimo(self.factory.post(f'/agendar/{self.link.token}/', dados)), self.link.token,
        )
        dados.update(nome='Segundo Cliente', telefone='84999990002')

        segunda = AgendaPublicaView().post(
            self.anonimo(self.factory.post(f'/agendar/{self.link.token}/', dados)), self.link.token,
        )

        self.assertEqual(primeira.status_code, 200)
        self.assertEqual(segunda.status_code, 409)
        self.assertFalse(Cliente.objects.filter(celular='84999990002').exists())
        self.assertEqual(Agendamento.objects.count(), 1)
