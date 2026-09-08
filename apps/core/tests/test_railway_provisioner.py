from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from apps.core.services.railway_provisioner import RailwayProvisioner


class RailwayProvisionerTests(SimpleTestCase):
    @override_settings(
        RAILWAY_PROJECT_TOKEN='token-central',
        RAILWAY_PROJECT_ID='project-central',
        RAILWAY_ENVIRONMENT_ID='environment-central',
        RAILWAY_SERVICE_ID='app-central',
        RAILWAY_CONTROL_SERVICE_ID='app-central',
    )
    def test_exclusao_usa_cliente_do_pool_e_remove_variavel_central(self):
        pool = SimpleNamespace(pk=7)
        banco = SimpleNamespace(
            railway_project_pool=pool,
            railway_database_service_id='tenant-id',
            railway_database_service_name='Banco Tenant',
            railway_volume_id='volume-id',
            database_url_env_var='TENANT_DATABASE_URL_TESTE',
        )
        pool_client = SimpleNamespace(
            find_service=lambda *args, **kwargs: {'id': 'tenant-id'},
            delete_service=Mock(),
            delete_volume=Mock(),
            volume_count=lambda: 3,
        )
        control_client = SimpleNamespace(delete_variable=Mock())
        with (
            patch.object(RailwayProvisioner, '_client_for_pool', return_value=pool_client),
            patch.object(RailwayProvisioner, '_control_client', return_value=control_client),
            patch('apps.core.services.railway_provisioner.RailwayPoolService.update_observation') as update,
        ):
            resultado = RailwayProvisioner.delete_postgres(banco)

        control_client.delete_variable.assert_called_once_with(
            'app-central', 'TENANT_DATABASE_URL_TESTE',
        )
        pool_client.delete_service.assert_called_once_with('tenant-id')
        pool_client.delete_volume.assert_called_once_with('volume-id')
        update.assert_called_once_with(pool, 3)
        self.assertEqual(resultado['service_id'], 'tenant-id')

    @override_settings(
        RAILWAY_PROJECT_TOKEN='token-central',
        RAILWAY_PROJECT_ID='project-central',
        RAILWAY_ENVIRONMENT_ID='environment-central',
        RAILWAY_CONTROL_SERVICE_ID='app-central',
    )
    def test_worker_sincroniza_urls_de_tenant_do_app_central(self):
        client = SimpleNamespace(service_variables=lambda service_id: {
            'TENANT_DATABASE_URL_NOVA': 'postgresql://tenant',
            'SECRET_KEY': 'nao-copiar',
        })
        with (
            patch.object(RailwayProvisioner, '_control_client', return_value=client),
            patch.dict('os.environ', {}, clear=True),
        ):
            sincronizadas = RailwayProvisioner.sync_tenant_variables_from_control_app()
            import os
            valor = os.environ.get('TENANT_DATABASE_URL_NOVA')
            segredo = os.environ.get('SECRET_KEY')

        self.assertEqual(sincronizadas, ['TENANT_DATABASE_URL_NOVA'])
        self.assertEqual(valor, 'postgresql://tenant')
        self.assertIsNone(segredo)

    @override_settings(RAILWAY_PROJECT_ID='project-stage')
    def test_servico_importado_e_identificado_pelo_id(self):
        payload = {
            'project': {
                'services': {
                    'edges': [
                        {'node': {'id': 'tenant-importado', 'name': 'Eureka Tenant'}},
                        {'node': {'id': 'outro-servico', 'name': 'Empresa Nova'}},
                    ],
                },
            },
        }
        with patch.object(RailwayProvisioner, '_graphql', return_value=payload):
            service = RailwayProvisioner._find_service(
                'lr-sports-50649395000126',
                service_id='tenant-importado',
            )

        self.assertEqual(service, {'id': 'tenant-importado', 'name': 'Eureka Tenant'})

    @override_settings(
        RAILWAY_PROJECT_TOKEN='token-stage',
        RAILWAY_PROJECT_ID='project-stage',
        RAILWAY_ENVIRONMENT_ID='environment-stage',
        RAILWAY_SERVICE_ID='app-stage',
        RAILWAY_TENANT_DATABASE_VOLUME_PATH='/var/lib/postgresql/data',
    )
    def test_provisionamento_reutiliza_servico_importado_sem_duplicar(self):
        banco = SimpleNamespace(
            empresa=SimpleNamespace(
                nome_fantasia='L&R SPORTS', razao_social='L&R SPORTS LTDA',
                cnpj='50649395000126', pk=3,
            ),
            database_url_env_var='TENANT_DATABASE_URL_EUREKA',
            railway_database_service_id='tenant-importado',
            railway_database_service_name='Eureka Tenant',
        )
        with (
            patch.object(
                RailwayProvisioner, '_find_service',
                return_value={'id': 'tenant-importado', 'name': 'Eureka Tenant'},
            ) as find_service,
            patch.object(
                RailwayProvisioner, '_find_volume',
                return_value={'id': 'volume-importado', 'name': 'postgres-volume'},
            ),
            patch.object(RailwayProvisioner, '_create_service') as create_service,
            patch.object(RailwayProvisioner, '_deploy_service') as deploy_service,
            patch.object(RailwayProvisioner, '_set_app_variable') as set_variable,
        ):
            result = RailwayProvisioner.provision_postgres(banco)

        find_service.assert_called_once_with(
            'lr-sports-50649395000126', service_id='tenant-importado',
        )
        create_service.assert_not_called()
        deploy_service.assert_not_called()
        set_variable.assert_called_once()
        self.assertIn('Eureka Tenant', set_variable.call_args.args[1])
        self.assertEqual(result['service_id'], 'tenant-importado')
        self.assertEqual(result['service_name'], 'Eureka Tenant')
        self.assertEqual(result['database_url'], '')
