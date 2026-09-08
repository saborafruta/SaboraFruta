from unittest.mock import Mock, patch

from django.http import HttpResponse
from django.template.loader import get_template
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.core.forms.admin_forms import RailwayProjectPoolAdminForm
from apps.core.models import (
    Empresa, EmpresaBanco, Filial, PerfilAcesso, RailwayProjectPool, Usuario,
)
from apps.core.views import admin_area


class CentralInfraViewsTests(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Visual Ltda',
            nome_fantasia='Empresa Visual',
            cnpj='11222333000144',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Matriz Visual Ltda',
            nome_fantasia='Matriz Visual',
            cnpj='11222333000145',
            uf='RN',
            is_matriz=True,
        )
        cls.pool = RailwayProjectPool.objects.create(
            nome='Projeto Principal',
            railway_project_id='project-main',
            railway_environment_id='environment-main',
            token_env_var='RAILWAY_PROJECT_TOKEN_MAIN',
        )
        cls.banco = EmpresaBanco.objects.create(
            empresa=cls.empresa,
            slug='empresa-visual-11222333000144',
            db_alias='empresa_visual_11222333000144',
            database_url_env_var='TENANT_DATABASE_URL_EMPRESA_VISUAL',
            railway_database_service_name='Banco Empresa Visual',
            railway_project_pool=cls.pool,
            status=EmpresaBanco.Status.ATIVO,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Administrador', is_admin=True,
        )
        cls.admin = Usuario.objects.create_superuser(
            email='central@example.com', nome='Admin Central', password='teste123456',
            empresa=cls.empresa, filial=cls.filial, perfil=perfil,
        )

    def setUp(self):
        self.factory = RequestFactory()
        self.superuser = Mock(is_authenticated=True, is_superuser=True)

    def _get(self, url):
        request = self.factory.get(url)
        request.user = self.superuser
        return request

    def test_rotas_usam_nomes_e_urls_da_central(self):
        self.assertEqual(
            reverse('core:admin_empresa_banco_list'),
            '/gestao/empresas/bancos/',
        )
        self.assertEqual(
            reverse('core:admin_railway_pool_list'),
            '/gestao/central/gestao-railway/',
        )
        self.assertEqual(
            reverse('core:admin_empresa_banco_detail', args=[self.banco.pk]),
            f'/gestao/empresas/bancos/{self.banco.pk}/',
        )
        self.assertEqual(
            reverse('core:admin_railway_pool_create'),
            '/gestao/central/gestao-railway/novo/',
        )

    def test_templates_customizados_compilam(self):
        for template in (
            'core/admin/empresa_banco_list.html',
            'core/admin/empresa_banco_detail.html',
            'core/admin/railway_pool_list.html',
            'core/admin/railway_pool_form.html',
            'core/admin/empresa_list.html',
            'core/admin/filial_list.html',
        ):
            self.assertIsNotNone(get_template(template))

    def test_telas_principais_renderizam_sem_erro(self):
        self.client.force_login(self.admin)
        urls = (
            reverse('core:admin_empresa_banco_list'),
            reverse('core:admin_empresa_banco_detail', args=[self.banco.pk]),
            reverse('core:admin_railway_pool_list'),
            reverse('core:admin_railway_pool_create'),
            reverse('core:admin_railway_pool_edit', args=[self.pool.pk]),
            reverse('core:admin_empresa_list'),
            reverse('core:admin_filial_list'),
        )
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

    @patch('apps.core.views.admin_area.render', return_value=HttpResponse('ok'))
    def test_bancos_lista_empresas_filiais_e_status(self, render_mock):
        response = admin_area.empresa_banco_list(
            self._get(reverse('core:admin_empresa_banco_list'))
        )

        self.assertEqual(response.status_code, 200)
        context = render_mock.call_args.args[2]
        row = list(context['page_obj'])[0]
        self.assertEqual(row['empresa'], self.empresa)
        self.assertEqual(row['banco'], self.banco)
        self.assertEqual(context['total'], 1)

    @patch('apps.core.views.admin_area.render', return_value=HttpResponse('ok'))
    def test_detalhe_do_banco_lista_so_filiais_da_empresa(self, render_mock):
        response = admin_area.empresa_banco_detail(
            self._get(reverse('core:admin_empresa_banco_detail', args=[self.banco.pk])),
            self.banco.pk,
        )

        self.assertEqual(response.status_code, 200)
        context = render_mock.call_args.args[2]
        self.assertEqual(context['banco'], self.banco)
        self.assertEqual(list(context['filiais']), [self.filial])

    @override_settings(RAILWAY_PROJECT_ID='project-main')
    def test_formulario_railway_valida_capacidade(self):
        form = RailwayProjectPoolAdminForm(data={
            'nome': 'Projeto Dois',
            'railway_project_id': 'project-two',
            'railway_environment_id': 'environment-two',
            'token_env_var': 'RAILWAY_PROJECT_TOKEN_02',
            'connection_mode': RailwayProjectPool.ConnectionMode.PUBLIC,
            'prioridade': 20,
            'max_volumes': 10,
            'volumes_reservados': 1,
        })
        self.assertTrue(form.is_valid(), form.errors)
