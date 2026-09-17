from pathlib import Path
from types import SimpleNamespace

from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from apps.core.models import (
    Empresa, Filial, Notificacao, NotificacaoLeitura, PerfilAcesso,
    Permissao, Usuario,
)
from apps.core.services.home import nome_rota_inicial, usuario_e_administrador


class RotaInicialTests(SimpleTestCase):
    def test_perfil_ativo_da_filial_decide_a_pagina(self):
        operador = SimpleNamespace(is_admin=False)
        administrador = SimpleNamespace(is_admin=True)
        usuario = SimpleNamespace(
            is_authenticated=True,
            is_superuser=False,
            _perfil_ativo=operador,
            perfil=administrador,
        )

        self.assertFalse(usuario_e_administrador(usuario))
        self.assertEqual(nome_rota_inicial(usuario), 'core:inicio')

        usuario._perfil_ativo = administrador
        self.assertTrue(usuario_e_administrador(usuario))
        self.assertEqual(nome_rota_inicial(usuario), 'core:dashboard')


class InicioVisualTests(SimpleTestCase):
    def test_tema_claro_usa_acoes_legiveis_e_icones_contextuais(self):
        raiz = Path(__file__).resolve().parents[1]
        template = (raiz / 'templates' / 'core' / 'inicio.html').read_text(encoding='utf-8')
        styles = (raiz / 'static' / 'core' / 'css' / 'inicio.css').read_text(encoding='utf-8')

        self.assertIn('?v=20260917-3', template)
        self.assertNotIn('quick-access-arrow', template)
        self.assertIn("{% if '/clientes/' in acesso.caminho %}", template)
        self.assertIn('body.tema-claro .manage-shortcuts', styles)
        self.assertIn('background:#1d4ed8; color:#fff;', styles)
        self.assertIn('body.tema-claro .quick-access-icon', styles)
        self.assertIn('background:#1e3a5f; color:#fff;', styles)
        self.assertIn('background-image:radial-gradient(circle,rgba(37,99,235,.2)', styles)
        self.assertNotIn('body.tema-claro .quick-access-icon { background:#9a3412', styles)


class InicioOperacionalTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            razao_social='Empresa Teste', nome_fantasia='L&R SPORTS',
            cnpj='11222333000181', regime_tributario='simples_nacional',
            codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Filial Natal',
            nome_fantasia='L&R SPORTS', cnpj='11222333000181',
            cidade='Natal', uf='RN', is_matriz=True,
        )
        self.perfil_operador = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Operador', is_admin=False,
        )
        self.perfil_admin = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Administrador', is_admin=True,
        )
        for modulo in ('cadastros', 'produtos', 'estoque', 'pdv'):
            Permissao.objects.create(
                perfil=self.perfil_operador, modulo=modulo, pode_ver=True,
            )
        self.operador = Usuario.objects.create_user(
            email='joao@teste.local', nome='João da Silva', password='senha12345',
            empresa=self.empresa, filial=self.filial, perfil=self.perfil_operador,
        )
        self.admin = Usuario.objects.create_user(
            email='admin@teste.local', nome='Admin', password='senha12345',
            empresa=self.empresa, filial=self.filial, perfil=self.perfil_admin,
        )

    def _client(self, usuario):
        client = Client()
        client.force_login(usuario)
        session = client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()
        return client

    def test_home_e_dashboard_do_operador_abrem_inicio(self):
        client = self._client(self.operador)

        raiz = client.get('/')
        dashboard = client.get(reverse('core:dashboard'))

        self.assertRedirects(raiz, reverse('core:inicio'), fetch_redirect_response=False)
        self.assertRedirects(dashboard, reverse('core:inicio'), fetch_redirect_response=False)

    def test_inicio_exibe_identidade_e_acessos_permitidos(self):
        response = self._client(self.operador).get(reverse('core:inicio'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'João')
        self.assertContains(response, 'Continuar trabalhando')
        self.assertContains(response, 'Acessos rápidos')
        self.assertContains(response, reverse('cadastros:cliente-list'))
        self.assertContains(response, reverse('produtos:produto-list'))
        self.assertContains(response, reverse('estoque:estoque-list'))
        self.assertContains(response, reverse('pdv:home'))
        self.assertNotContains(response, '👋')

    def test_administrador_permanece_no_dashboard(self):
        client = self._client(self.admin)

        self.assertRedirects(
            client.get('/'), reverse('core:dashboard'), fetch_redirect_response=False,
        )
        self.assertRedirects(
            client.get(reverse('core:inicio')),
            reverse('core:dashboard'), fetch_redirect_response=False,
        )

    def test_notificacao_pode_ser_marcada_como_lida_sem_abrir(self):
        notificacao = Notificacao.objects.create(
            filial=self.filial, tipo=Notificacao.Tipo.ALERTA_SISTEMA,
            titulo='Alteração no pedido #000123',
        )
        client = self._client(self.operador)
        response = client.post(
            reverse('core:notificacao-marcar-lida', args=[notificacao.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(NotificacaoLeitura.objects.filter(
            notificacao=notificacao, usuario=self.operador,
        ).exists())
        pagina = client.get(reverse('core:inicio'))
        self.assertEqual(list(pagina.context['notificacoes_pendentes']), [])
