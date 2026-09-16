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
