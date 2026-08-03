"""
Encerramento remoto de sessões.

Tres coisas que precisam valer: derruba mesmo, derruba SO o alvo, e nao
desloga quem clicou. Errar a terceira transformaria um botao de lista numa
armadilha; errar a segunda seria bem pior.
"""
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.core.services.sessoes import encerrar_sessoes, sessoes_do_usuario


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BaseSessoes(TestCase):
    SENHA = 'senha-de-teste-123'
    _seq = 0

    def setUp(self):
        from apps.core.models import Empresa, Filial

        self.empresa = Empresa.objects.create(
            razao_social='T', cnpj='11222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Matriz', nome_fantasia='Matriz',
            cnpj='11222333000181', uf='RN', is_matriz=True,
        )
        self.admin = self._usuario('Admin', admin=True)
        self.motorista = self._usuario('Leonardo')

    def _usuario(self, nome, *, admin=False):
        from apps.core.models import PerfilAcesso, Usuario

        BaseSessoes._seq += 1
        n = BaseSessoes._seq
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome=f'Perfil {n}', is_admin=True,
        )
        return Usuario.objects.create_user(
            email=f'u{n}@teste.local', nome=nome, password=self.SENHA,
            empresa=self.empresa, perfil=perfil, filial=self.filial,
            is_staff=admin, is_superuser=admin,
        )

    def _aparelho(self, usuario):
        """Um cliente novo = um aparelho novo, com sessão própria."""
        c = Client()
        c.force_login(usuario)
        return c


class ServicoTests(BaseSessoes):
    def test_encontra_as_sessoes_do_usuario(self):
        self._aparelho(self.motorista)
        self._aparelho(self.motorista)

        self.assertEqual(len(sessoes_do_usuario(self.motorista)), 2)

    def test_nao_confunde_sessao_de_outro_usuario(self):
        self._aparelho(self.motorista)
        self._aparelho(self.admin)

        self.assertEqual(len(sessoes_do_usuario(self.motorista)), 1)

    def test_encerrar_apaga_todas(self):
        self._aparelho(self.motorista)
        self._aparelho(self.motorista)

        self.assertEqual(encerrar_sessoes(self.motorista), 2)
        self.assertEqual(sessoes_do_usuario(self.motorista), [])

    def test_encerrar_nao_toca_nas_sessoes_alheias(self):
        """O erro grave seria derrubar a empresa inteira num clique."""
        self._aparelho(self.motorista)
        outro = self._aparelho(self.admin)

        encerrar_sessoes(self.motorista)

        self.assertEqual(len(sessoes_do_usuario(self.admin)), 1)
        self.assertEqual(outro.get(reverse('core:dashboard')).status_code, 200)

    def test_preservar_mantem_a_sessao_indicada(self):
        manter = self._aparelho(self.motorista)
        self._aparelho(self.motorista)
        chave = manter.session.session_key

        self.assertEqual(encerrar_sessoes(self.motorista, preservar=chave), 1)
        self.assertEqual(sessoes_do_usuario(self.motorista), [chave])

    def test_sem_sessao_ativa_devolve_zero(self):
        self.assertEqual(encerrar_sessoes(self.motorista), 0)

    def test_sessao_corrompida_nao_impede_as_demais(self):
        """
        Sessao assinada com uma SECRET_KEY antiga nao pode travar o botao --
        as outras precisam cair mesmo assim.
        """
        self._aparelho(self.motorista)
        Session.objects.create(
            session_key='lixo1234567890abcdef',
            session_data='isto-nao-decodifica',
            expire_date=Session.objects.first().expire_date,
        )

        self.assertEqual(encerrar_sessoes(self.motorista), 1)


class ViewTests(BaseSessoes):
    def _url(self, usuario):
        return reverse('core:admin_usuario_encerrar_sessoes', args=[usuario.pk])

    def test_botao_derruba_o_usuario_alvo(self):
        aparelho = self._aparelho(self.motorista)
        self.assertEqual(aparelho.get(reverse('core:dashboard')).status_code, 200)

        painel = self._aparelho(self.admin)
        painel.post(self._url(self.motorista))

        # Sem sessao, o acesso vira redirecionamento para o login.
        self.assertEqual(aparelho.get(reverse('core:dashboard')).status_code, 302)

    def test_quem_clicou_continua_logado(self):
        """Encerrar as proprias sessoes nao pode deslogar quem clicou."""
        painel = self._aparelho(self.admin)
        self._aparelho(self.admin)          # outro aparelho do mesmo admin

        painel.post(self._url(self.admin))

        self.assertEqual(painel.get(reverse('core:dashboard')).status_code, 200)
        self.assertEqual(len(sessoes_do_usuario(self.admin)), 1)

    def test_get_nao_encerra(self):
        """Acao destrutiva por link seria disparada por prefetch do navegador."""
        aparelho = self._aparelho(self.motorista)
        painel = self._aparelho(self.admin)

        painel.get(self._url(self.motorista))

        self.assertEqual(len(sessoes_do_usuario(self.motorista)), 1)
        self.assertEqual(aparelho.get(reverse('core:dashboard')).status_code, 200)

    def test_exige_area_administrativa(self):
        comum = self._usuario('Comum')
        cliente = self._aparelho(comum)

        resp = cliente.post(self._url(self.motorista))

        self.assertIn(resp.status_code, (302, 403))
        self.assertEqual(len(sessoes_do_usuario(self.motorista)), 0)

    def test_exige_autenticacao(self):
        self._aparelho(self.motorista)
        anonimo = Client()

        anonimo.post(self._url(self.motorista))

        self.assertEqual(len(sessoes_do_usuario(self.motorista)), 1)

    def test_botao_aparece_na_lista(self):
        painel = self._aparelho(self.admin)
        resp = painel.get(reverse('core:admin_usuario_list'))

        self.assertContains(resp, 'Encerrar sessoes')
