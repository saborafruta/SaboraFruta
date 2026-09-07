from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings

from apps.core.models import Empresa, EmpresaBanco, RailwayProjectPool
from apps.core.services.railway_pool_service import (
    RailwayPoolService,
    RailwayPoolUnavailableError,
)
from apps.core.services.railway_provisioner import RailwayProvisioner


class RailwayPoolServiceTests(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Multi Projeto Ltda',
            nome_fantasia='Empresa Multi',
            cnpj='12345678000199',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.banco = EmpresaBanco.objects.create(
            empresa=cls.empresa,
            slug='empresa-multi-12345678000199',
            db_alias='empresa_multi_12345678000199',
            database_url_env_var='TENANT_DATABASE_URL_EMPRESA_MULTI_12345678000199',
        )

    @override_settings(RAILWAY_MULTI_PROJECT_ENABLED=False)
    def test_feature_flag_desligada_preserva_fluxo_legado(self):
        pool = RailwayProjectPool.objects.create(
            nome='Bancos 02',
            railway_project_id='project-02',
            railway_environment_id='environment-02',
            token_env_var='RAILWAY_PROJECT_TOKEN_02',
            ativo=True,
            status=RailwayProjectPool.Status.ATIVO,
        )
        self.banco.railway_project_pool = pool
        self.banco.save(update_fields=['railway_project_pool'])

        self.assertIsNone(RailwayPoolService.select_for_banco(self.banco))

    @override_settings(RAILWAY_MULTI_PROJECT_ENABLED=True)
    def test_seleciona_pool_por_prioridade_e_reserva_vaga(self):
        RailwayProjectPool.objects.create(
            nome='Bancos 03',
            railway_project_id='project-03',
            railway_environment_id='environment-03',
            token_env_var='RAILWAY_PROJECT_TOKEN_03',
            prioridade=30,
            max_volumes=10,
            volumes_reservados=1,
            ativo=True,
            status=RailwayProjectPool.Status.ATIVO,
        )
        preferido = RailwayProjectPool.objects.create(
            nome='Bancos 02',
            railway_project_id='project-02',
            railway_environment_id='environment-02',
            token_env_var='RAILWAY_PROJECT_TOKEN_02',
            prioridade=20,
            max_volumes=10,
            volumes_reservados=1,
            ultimo_total_volumes=8,
            ativo=True,
            status=RailwayProjectPool.Status.ATIVO,
        )

        selected = RailwayPoolService.select_for_banco(self.banco)

        self.banco.refresh_from_db()
        self.assertEqual(selected, preferido)
        self.assertEqual(self.banco.railway_project_pool, preferido)

    @override_settings(RAILWAY_MULTI_PROJECT_ENABLED=True)
    def test_nao_seleciona_pool_sem_vaga_operacional(self):
        RailwayProjectPool.objects.create(
            nome='Bancos lotado',
            railway_project_id='project-full',
            railway_environment_id='environment-full',
            token_env_var='RAILWAY_PROJECT_TOKEN_FULL',
            max_volumes=10,
            volumes_reservados=1,
            ultimo_total_volumes=9,
            ativo=True,
            status=RailwayProjectPool.Status.ATIVO,
        )

        with self.assertRaises(RailwayPoolUnavailableError):
            RailwayPoolService.select_for_banco(self.banco)

    @override_settings(RAILWAY_PROJECT_ID='project-principal')
    @patch.dict('os.environ', {'RAILWAY_PROJECT_TOKEN_02': 'remote-token'})
    @patch('apps.core.services.railway_provisioner.RailwayApiClient')
    def test_nao_ativa_rede_privada_de_outro_projeto(self, api_client):
        pool = RailwayProjectPool.objects.create(
            nome='Bancos privados inválidos',
            railway_project_id='project-02',
            railway_environment_id='environment-02',
            token_env_var='RAILWAY_PROJECT_TOKEN_02',
            connection_mode=RailwayProjectPool.ConnectionMode.PRIVATE,
        )

        success, message = RailwayPoolService.validate_and_activate(pool)

        pool.refresh_from_db()
        self.assertFalse(success)
        self.assertFalse(pool.ativo)
        self.assertEqual(pool.status, RailwayProjectPool.Status.ERRO)
        self.assertIn('Rede privada', message)
        api_client.assert_not_called()


class RailwayMultiProjectProvisionerTests(TestCase):
    databases = {'default'}

    @override_settings(
        RAILWAY_MULTI_PROJECT_ENABLED=True,
        RAILWAY_PROJECT_TOKEN='control-token',
        RAILWAY_CONTROL_PROJECT_TOKEN='control-token',
        RAILWAY_PROJECT_ID='control-project',
        RAILWAY_ENVIRONMENT_ID='control-environment',
        RAILWAY_SERVICE_ID='control-app',
        RAILWAY_TENANT_DATABASE_VOLUME_PATH='/var/lib/postgresql/data',
    )
    @patch.dict('os.environ', {'RAILWAY_PROJECT_TOKEN_02': 'remote-token'})
    def test_provisiona_banco_remoto_com_proxy_ssl(self):
        pool = SimpleNamespace(
            pk=2,
            railway_project_id='remote-project',
            railway_environment_id='remote-environment',
            token_env_var='RAILWAY_PROJECT_TOKEN_02',
            connection_mode=RailwayProjectPool.ConnectionMode.PUBLIC,
        )
        banco = SimpleNamespace(
            empresa=SimpleNamespace(
                nome_fantasia='Empresa Remota', razao_social='Empresa Remota Ltda',
                cnpj='11222333000144', pk=7,
            ),
            database_url_env_var='TENANT_DATABASE_URL_EMPRESA_REMOTA',
            railway_database_service_id='',
            railway_database_service_name='',
            railway_volume_id='',
            railway_tcp_proxy_id='',
            railway_tcp_proxy_domain='',
            railway_tcp_proxy_port=None,
        )
        remote_client = Mock()
        remote_client.find_service.return_value = None
        remote_client.create_service.return_value = {
            'id': 'database-service', 'name': 'empresa-remota-11222333000144',
        }
        remote_client.find_volume.return_value = None
        remote_client.create_volume.return_value = {'id': 'volume-id', 'name': 'volume'}
        remote_client.ensure_tcp_proxy.return_value = {
            'id': 'proxy-id', 'domain': 'proxy.railway.test',
            'proxyPort': 15432, 'applicationPort': 5432,
        }
        remote_client.volume_count.return_value = 1

        with (
            patch.object(RailwayPoolService, 'select_for_banco', return_value=pool),
            patch.object(RailwayPoolService, 'update_observation') as observation,
            patch.object(RailwayProvisioner, '_client_for_pool', return_value=remote_client),
            patch.object(RailwayProvisioner, '_set_app_variable') as set_variable,
        ):
            result = RailwayProvisioner.provision_postgres(banco)

        connection_url = set_variable.call_args.args[1]
        self.assertIn('proxy.railway.test:15432', connection_url)
        self.assertIn('sslmode=require', connection_url)
        self.assertNotIn('${{', connection_url)
        self.assertEqual(result['connection_mode'], 'public')
        self.assertEqual(banco.railway_volume_id, 'volume-id')
        self.assertEqual(banco.railway_tcp_proxy_id, 'proxy-id')
        remote_client.deploy_service.assert_called_once_with('database-service')
        observation.assert_called_once_with(pool, 1)

    def test_url_publica_escapa_credenciais(self):
        url = RailwayProvisioner._public_database_url(
            {'domain': 'proxy.test', 'proxyPort': 1234},
            {
                'POSTGRES_USER': 'user@corp',
                'POSTGRES_PASSWORD': 'p:a/ss',
                'POSTGRES_DB': 'railway',
            },
        )

        self.assertEqual(
            url,
            'postgresql://user%40corp:p%3Aa%2Fss@proxy.test:1234/railway?sslmode=require',
        )
