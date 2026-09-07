from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.core.services.railway_provisioner import RailwayProvisioner


class RailwayProvisionerTests(SimpleTestCase):
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
