from pathlib import Path
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.core.forms.admin_forms import UsuarioAdminForm
from apps.core.models import (
    Empresa,
    Filial,
    PerfilAcesso,
    Usuario,
    UsuarioFilialAcesso,
)
from apps.core.views import admin_area


class UsuarioAdminFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Teste LTDA',
            nome_fantasia='Empresa Teste',
            cnpj='12345678000195',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Teste',
            nome_fantasia='Filial Teste',
            cnpj='12345678000276',
            cidade='Natal',
            uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Administrador',
            is_admin=True,
        )
        cls.outra_empresa = Empresa.objects.create(
            razao_social='Outra Empresa LTDA',
            nome_fantasia='Outra Empresa',
            cnpj='98765432000198',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.outra_filial = Filial.objects.create(
            empresa=cls.outra_empresa,
            razao_social='Outra Filial',
            nome_fantasia='Outra Filial',
            cnpj='98765432000279',
            cidade='Recife',
            uf='PE',
        )
        cls.outro_perfil = PerfilAcesso.objects.create(
            empresa=cls.outra_empresa,
            nome='Gestor',
            is_admin=True,
        )
        cls.superuser = Usuario.objects.create_superuser(
            email='admin@teste.local',
            nome='Admin',
            password='senha-segura',
            empresa=cls.empresa,
            perfil=cls.perfil,
        )

    def _dados(self, email='thalmo@ited.com'):
        return {
            'central': '1',
            'empresa': str(self.empresa.pk),
            'filial': '',
            'perfil': str(self.perfil.pk),
            'nome': 'Thalmo',
            'cpf': '',
            'email': email,
            'telefone': '',
            'senha': 'senha-nova-segura',
            'senha_confirmacao': 'senha-nova-segura',
            'comissao_percentual': '0',
            'pin_code': '',
            'ativo': 'on',
            'replicar_para_filiais': [str(self.filial.pk)],
            f'perfil_empresa_{self.empresa.pk}': str(self.perfil.pk),
        }

    def test_acesso_replicado_sem_filial_principal_nao_grava_nulo(self):
        parcial = Usuario.objects.create_user(
            email='thalmo@ited.com',
            nome='Thalmo',
            password='senha-anterior',
            empresa=self.empresa,
            perfil=self.perfil,
            filial=None,
        )
        form = UsuarioAdminForm(
            data=self._dados(),
            actor=self.superuser,
            scope_filial=None,
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        usuario = form.save()

        self.assertEqual(usuario.pk, parcial.pk)
        acesso = UsuarioFilialAcesso.objects.get(usuario=usuario, filial=self.filial)
        self.assertIs(acesso.is_padrao, False)

    def test_view_desfaz_usuario_se_gravacao_de_acesso_falhar(self):
        email = 'novo@ited.com'
        request = RequestFactory().post(
            reverse('core:admin_usuario_create') + '?central=1',
            data=self._dados(email=email),
        )
        request.user = self.superuser

        with patch.object(UsuarioAdminForm, '_salvar_acessos', side_effect=RuntimeError('falha simulada')):
            with self.assertRaises(RuntimeError):
                admin_area.usuario_form(request)

        self.assertFalse(Usuario.objects.filter(email=email).exists())

    def test_superusuario_define_perfil_independente_em_outra_empresa(self):
        dados = self._dados(email='multempresa@ited.com')
        dados['replicar_para_filiais'] = [str(self.outra_filial.pk)]
        dados[f'perfil_empresa_{self.outra_empresa.pk}'] = str(self.outro_perfil.pk)
        form = UsuarioAdminForm(
            data=dados,
            actor=self.superuser,
            scope_filial=None,
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        usuario = form.save()

        acesso = UsuarioFilialAcesso.objects.get(usuario=usuario, filial=self.outra_filial)
        self.assertEqual(acesso.perfil, self.outro_perfil)
        self.assertIs(acesso.is_padrao, False)

    def test_edicao_global_desativa_acesso_desmarcado(self):
        usuario = Usuario.objects.create_user(
            email='editar@ited.com',
            nome='Editar',
            password='senha-anterior',
            empresa=self.empresa,
            perfil=self.perfil,
            filial=None,
        )
        acesso = UsuarioFilialAcesso.objects.create(
            usuario=usuario,
            filial=self.outra_filial,
            perfil=self.outro_perfil,
            ativo=True,
        )
        dados = self._dados(email=usuario.email)
        dados['replicar_para_filiais'] = []
        form = UsuarioAdminForm(
            data=dados,
            instance=usuario,
            actor=self.superuser,
            scope_filial=None,
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()

        acesso.refresh_from_db()
        self.assertFalse(acesso.ativo)
        self.assertFalse(acesso.is_padrao)

    def test_edicao_global_mostra_acessos_atuais_agrupados(self):
        usuario = Usuario.objects.create_user(
            email='grupos@ited.com',
            nome='Grupos',
            password='senha-anterior',
            empresa=self.empresa,
            perfil=self.perfil,
            filial=None,
        )
        UsuarioFilialAcesso.objects.create(
            usuario=usuario,
            filial=self.outra_filial,
            perfil=self.outro_perfil,
            ativo=True,
        )

        form = UsuarioAdminForm(
            instance=usuario,
            actor=self.superuser,
            scope_filial=None,
        )

        grupo = next(item for item in form.access_groups if item['empresa'] == self.outra_empresa)
        self.assertEqual(grupo['selected_count'], 1)
        self.assertEqual(form.initial['replicar_para_filiais'], [self.outra_filial.pk])
        self.assertEqual(form.fields[f'perfil_empresa_{self.outra_empresa.pk}'].initial, self.outro_perfil.pk)

    def test_template_tem_busca_de_empresa_e_perfil_por_grupo(self):
        raiz = Path(__file__).resolve().parents[1]
        template = (raiz / 'templates' / 'core' / 'admin' / 'usuario_form_sections.html').read_text(
            encoding='utf-8',
        )

        self.assertIn('Buscar empresa por nome ou CNPJ...', template)
        self.assertIn('form.access_groups', template)
        self.assertIn('Perfil nesta empresa', template)

    def test_tela_global_renderiza_busca_e_empresas(self):
        self.client.force_login(self.superuser)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

        response = self.client.get(reverse('core:admin_usuario_create') + '?central=1')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Buscar empresa por nome ou CNPJ...')
        self.assertContains(response, self.outra_empresa.nome_fantasia)
        self.assertContains(response, self.outra_empresa.cnpj)
        self.assertContains(response, self.outra_filial.nome_fantasia)
