from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from apps.core.forms.parametros import FilialIdentidadeForm, ParametrosSistemaForm
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.models.parametros import ParametrosSistema
from apps.core.views.parametros import parametros_sistema
from apps.fiscal.services.focusnfe_service import FocusNFeService


class ParametrosSistemaAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Parametros LTDA',
            nome_fantasia='Empresa Parametros',
            cnpj='72345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Parametros',
            nome_fantasia='Filial Parametros',
            cnpj='72345678000192',
            uf='RN',
        )
        cls.perfil_admin = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Administrador',
            is_admin=True,
        )
        cls.perfil_operador = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome='Operador',
            is_admin=False,
        )
        cls.admin = Usuario.objects.create_user(
            email='admin-parametros@inoovated.com',
            nome='Admin Parametros',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil_admin,
        )
        cls.operador = Usuario.objects.create_user(
            email='operador-parametros@inoovated.com',
            nome='Operador Parametros',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil_operador,
        )

    def test_admin_acessa_parametros(self):
        request = RequestFactory().get('/gestao/parametros/')
        request.user = self.admin
        request.user._perfil_ativo = self.perfil_admin
        request.filial_ativa = self.filial

        with patch('apps.core.views.parametros.render', return_value=HttpResponse('ok')) as render_mock:
            response = parametros_sistema(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(render_mock.called)

    def test_operador_nao_acessa_parametros(self):
        request = RequestFactory().get('/gestao/parametros/')
        request.user = self.operador
        request.user._perfil_ativo = self.perfil_operador
        request.filial_ativa = self.filial

        with self.assertRaises(PermissionDenied):
            parametros_sistema(request)

    def test_formulario_nao_expoe_segredos_e_preserva_campos_vazios(self):
        self.filial.focusnfe_token = 'token-focus-secreto'
        self.filial.save(update_fields=['focusnfe_token'])
        params = ParametrosSistema.objects.create(
            filial=self.filial,
            senha_certificado='senha-certificado',
            focusnfe_token_principal='token-principal-secreto',
            nfce_csc_token='csc-secreto',
        )

        form_filial = FilialIdentidadeForm(instance=self.filial)
        form_params = ParametrosSistemaForm(instance=params)

        self.assertNotIn('token-focus-secreto', form_filial.as_p())
        self.assertNotIn('senha-certificado', form_params.as_p())
        self.assertNotIn('token-principal-secreto', form_params.as_p())
        self.assertNotIn('csc-secreto', form_params.as_p())

        filial_data = {
            field: form_filial.initial.get(field, '')
            for field in form_filial.fields
        }
        filial_data['focusnfe_token'] = ''
        filial_data['cnpj'] = self.filial.cnpj
        filial_post = FilialIdentidadeForm(filial_data, instance=self.filial)
        self.assertTrue(filial_post.is_valid(), filial_post.errors)
        self.assertEqual(filial_post.cleaned_data['focusnfe_token'], 'token-focus-secreto')

        params_data = {
            field: form_params.initial.get(field, '')
            for field in form_params.fields
            if field != 'certificado_digital'
        }
        params_data.update({
            'senha_certificado': '',
            'focusnfe_token_principal': '',
            'nfce_csc_token': '',
        })
        params_post = ParametrosSistemaForm(params_data, instance=params)
        self.assertTrue(params_post.is_valid(), params_post.errors)
        self.assertEqual(params_post.cleaned_data['senha_certificado'], 'senha-certificado')
        self.assertEqual(
            params_post.cleaned_data['focusnfe_token_principal'],
            'token-principal-secreto',
        )
        self.assertEqual(params_post.cleaned_data['nfce_csc_token'], 'csc-secreto')

    @patch('apps.fiscal.integrations.focusnfe.FocusNFeClient')
    @patch('apps.fiscal.integrations.focusnfe.config.FocusNFeConfig.from_env')
    def test_sincronizacao_usa_token_principal_no_endpoint_producao(
        self,
        config_mock,
        client_mock,
    ):
        config_mock.return_value = object()
        client_mock.return_value.empresas.upsert.return_value = {'id': 'empresa-focus'}
        filial = SimpleNamespace(
            cnpj='14004764000240',
            razao_social='SaboraFruta',
            nome_fantasia='SaboraFruta',
            inscricao_estadual='207184704',
            codigo_regime_tributario=1,
            empresa=None,
            endereco='Avenida Teste',
            numero='100',
            complemento='',
            bairro='Centro',
            cep='59000000',
            cidade='Natal',
            uf='RN',
            focusnfe_ambiente=1,
        )
        params = SimpleNamespace(
            focusnfe_token_principal='token-principal',
            certificado_base64='',
            certificado_digital=None,
            senha_certificado='',
            nfce_csc_token='csc-producao',
            nfce_csc_id='2',
        )

        FocusNFeService().sincronizar_empresa(filial, params)

        config_mock.assert_called_once_with(token='token-principal', ambiente=1)
        payload = client_mock.return_value.empresas.upsert.call_args.args[1]
        self.assertEqual(payload['csc_nfce_producao'], 'csc-producao')
        self.assertEqual(payload['id_token_nfce_producao'], '2')
