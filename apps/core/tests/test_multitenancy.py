from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ImproperlyConfigured
from django.contrib.sessions.models import Session
from django.test import RequestFactory, TestCase, override_settings

from apps.core.db_router import TenantDatabaseRouter
from apps.core.middleware.tenant import TenantContextMiddleware
from apps.core.models import (
    Empresa, EmpresaBanco, Filial, PerfilAcesso, TenantPublicLink, Usuario,
)
from apps.core.services.empresa_banco_service import EmpresaBancoService
from apps.core.services.tenant_public_link_service import TenantPublicLinkService
from apps.core.services.tenant_task_service import TenantTaskService
from apps.core.tenant_context import get_current_tenant_db, tenant_db


class MultitenancyFoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Tenant LTDA',
            nome_fantasia='Empresa Tenant',
            cnpj='99888777000166',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Tenant',
            nome_fantasia='Filial Tenant',
            cnpj='99888777000167',
            uf='RN',
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Operador Tenant', is_admin=False,
        )
        cls.usuario = Usuario.objects.create_user(
            email='tenant@example.com',
            nome='Usuário Tenant',
            password='teste123456',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=cls.perfil,
        )
        cls.banco = EmpresaBanco.objects.create(
            empresa=cls.empresa,
            slug='empresa-tenant-99888777000166',
            db_alias='empresa_tenant_99888777000166',
            database_url_env_var='TENANT_DATABASE_URL_EMPRESA_TENANT_99888777000166',
            status=EmpresaBanco.Status.ATIVO,
        )

    def test_feature_flag_desligada_preserva_banco_atual(self):
        router = TenantDatabaseRouter()
        with tenant_db(self.banco.db_alias):
            self.assertIsNone(router.db_for_read(Empresa))
            self.assertIsNone(router.db_for_write(Empresa))

    @override_settings(
        TENANT_DATABASE_ROUTING_ENABLED=True,
        TENANT_DATABASE_ALIASES=['empresa_tenant_99888777000166'],
    )
    def test_modelo_operacional_vai_para_banco_da_empresa(self):
        router = TenantDatabaseRouter()
        with tenant_db(self.banco.db_alias):
            self.assertEqual(router.db_for_read(Empresa), self.banco.db_alias)
            self.assertEqual(router.db_for_write(Empresa), self.banco.db_alias)
            self.assertEqual(router.db_for_read(EmpresaBanco), 'default')
            self.assertEqual(router.db_for_read(Session), 'default')
        self.assertIsNone(get_current_tenant_db())

    @override_settings(
        TENANT_DATABASE_ROUTING_ENABLED=True,
        TENANT_PUBLIC_LINK_ROUTING_READY=True,
        TENANT_BACKGROUND_TASKS_READY=True,
    )
    def test_alias_inativo_e_recusado_sem_consultar_banco_operacional(self):
        self.banco.status = EmpresaBanco.Status.INATIVO
        self.banco.save(update_fields=['status'])
        request = RequestFactory().get('/dashboard/')
        request.session = {
            'tenant_db_alias': self.banco.db_alias,
            'filial_ativa_id': self.filial.pk,
        }
        request.user = SimpleNamespace(is_authenticated=False)

        response = TenantContextMiddleware(lambda req: None)(request)

        self.assertEqual(response.status_code, 302)
        self.assertNotIn('tenant_db_alias', request.session)
        self.assertNotIn('filial_ativa_id', request.session)

    def test_ensure_for_empresa_nao_cria_duplicado(self):
        banco, created = EmpresaBancoService.ensure_for_empresa(self.empresa)

        self.assertFalse(created)
        self.assertEqual(banco.pk, self.banco.pk)

    @override_settings(TENANT_DATABASE_PROVISIONING_MODE='manual')
    def test_modo_manual_nao_cria_recurso_no_railway(self):
        self.banco.provisionamento_modo = 'manual'
        with patch(
            'apps.core.services.empresa_banco_service.RailwayProvisioner.provision_postgres'
        ) as provision:
            ok, message = EmpresaBancoService.solicitar_provisionamento(self.banco)

        self.assertFalse(ok)
        self.assertIn(self.banco.database_url_env_var, message)
        provision.assert_not_called()
        self.banco.refresh_from_db()
        self.assertEqual(
            self.banco.status, EmpresaBanco.Status.AGUARDANDO_CONFIGURACAO,
        )

    @override_settings(
        TENANT_DATABASE_ROUTING_ENABLED=True,
        TENANT_PUBLIC_LINK_ROUTING_READY=False,
        TENANT_BACKGROUND_TASKS_READY=True,
    )
    def test_middleware_bloqueia_ativacao_incompleta(self):
        with self.assertRaises(ImproperlyConfigured):
            TenantContextMiddleware(lambda request: None)

    def test_indice_de_link_publico_nao_guarda_token_aberto(self):
        token = 'segredo-publico-123'
        TenantPublicLink.objects.create(
            tipo='pdv',
            token_hash=TenantPublicLinkService.token_hash(token),
            db_alias=self.banco.db_alias,
        )

        registro = TenantPublicLink.objects.get(tipo='pdv')
        self.assertNotEqual(registro.token_hash, token)
        self.assertEqual(len(registro.token_hash), 64)

    def test_identifica_todas_as_familias_de_links_publicos(self):
        casos = {
            '/comprovante/abc/pdf/': ('pdv', 'abc'),
            '/cardapio/mesa-123/pedido/': ('cardapio', 'mesa-123'),
            '/pedido/token-456/responder/': ('pedido', 'token-456'),
            '/pedido/entrega/EXP-789/': ('entrega', 'EXP-789'),
        }
        for path, esperado in casos.items():
            with self.subTest(path=path):
                rota = TenantPublicLinkService.route_for_path(path)
                self.assertEqual((rota[0], rota[3]), esperado)

    @override_settings(TENANT_DATABASE_ROUTING_ENABLED=False)
    def test_task_preserva_execucao_unica_com_flag_desligada(self):
        callback = lambda: 7

        self.assertEqual(TenantTaskService.executar_em_todos(callback), 7)

    @override_settings(TENANT_DATABASE_ROUTING_ENABLED=True)
    def test_task_ativa_o_contexto_de_cada_empresa(self):
        callback = Mock(side_effect=lambda: 3 if get_current_tenant_db() == self.banco.db_alias else 0)
        conexao = Mock()
        queryset = Mock()
        queryset.order_by.return_value.iterator.return_value = [self.banco]

        with (
            patch.object(EmpresaBanco.objects, 'using') as using,
            patch(
                'apps.core.services.tenant_task_service.register_tenant_database',
                return_value=True,
            ),
            patch(
                'apps.core.services.tenant_task_service.connections',
                {self.banco.db_alias: conexao},
            ),
        ):
            using.return_value.filter.return_value = queryset
            total = TenantTaskService.executar_em_todos(callback)

        self.assertEqual(total, 3)
        callback.assert_called_once_with()
        conexao.close.assert_called_once_with()
        self.assertIsNone(get_current_tenant_db())
