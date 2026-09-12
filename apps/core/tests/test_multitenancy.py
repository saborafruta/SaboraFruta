import os
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.contrib.sessions.models import Session
from django.test import RequestFactory, TestCase, override_settings
from django.utils.functional import SimpleLazyObject

from apps.core.db_router import TenantDatabaseRouter
from apps.core.middleware.filial import FilialMiddleware
from apps.core.middleware.tenant import TenantContextMiddleware
from apps.core.models import (
    Empresa, EmpresaBanco, Filial, PerfilAcesso, TenantPublicLink, Usuario,
)
from apps.core.services.empresa_banco_service import EmpresaBancoService
from apps.core.services.auth_service import AuthService
from apps.core.services.exceptions import DadosInvalidosError
from apps.core.services.railway_provisioner import RailwayProvisioner
from apps.core.services.tenant_public_link_service import TenantPublicLinkService
from apps.core.services.tenant_task_service import TenantTaskService
from apps.core.tenant_context import get_current_tenant_db, tenant_atomic, tenant_db
from apps.core.views.auth import SelecionarFilialView
from apps.core.views.admin_area import central_administrativa


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

    def test_tenant_atomic_usa_a_mesma_conexao_do_router(self):
        callback = Mock(return_value='ok')

        with patch('apps.core.tenant_context.transaction.atomic') as atomic:
            with tenant_db(self.banco.db_alias):
                resultado = tenant_atomic(callback)()

        self.assertEqual(resultado, 'ok')
        atomic.assert_called_once_with(using=self.banco.db_alias)
        callback.assert_called_once_with()

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

    def test_verificacao_nao_rebaixa_banco_ativo(self):
        conexao = MagicMock()
        fake_connections = MagicMock()
        fake_connections.databases = {self.banco.db_alias: {}}
        fake_connections.__getitem__.return_value = conexao

        with (
            patch(
                'apps.core.services.empresa_banco_service.register_tenant_database',
                return_value=True,
            ),
            patch(
                'apps.core.services.empresa_banco_service.connections',
                fake_connections,
            ),
        ):
            ok, _message = EmpresaBancoService.testar_conexao(self.banco)

        self.assertTrue(ok)
        self.banco.refresh_from_db()
        self.assertEqual(self.banco.status, EmpresaBanco.Status.ATIVO)

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

    @override_settings(
        TENANT_DATABASE_ROUTING_ENABLED=True,
        TENANT_PUBLIC_LINK_ROUTING_READY=True,
        TENANT_BACKGROUND_TASKS_READY=True,
    )
    def test_middleware_substitui_usuario_central_pelo_usuario_do_tenant(self):
        request = RequestFactory().get('/dashboard/')
        request.session = {'tenant_db_alias': self.banco.db_alias}
        contextos_ao_carregar_sessao = []

        def carregar_usuario_central():
            contextos_ao_carregar_sessao.append(get_current_tenant_db())
            return self.usuario

        request.user = SimpleLazyObject(carregar_usuario_central)
        tenant_user = Mock(email=self.usuario.email, is_authenticated=True)
        tenant_manager = Mock()
        tenant_manager.get.return_value = tenant_user

        with (
            patch(
                'apps.core.middleware.tenant.register_tenant_database',
                return_value=True,
            ),
            patch.object(Usuario.objects, 'using', return_value=tenant_manager),
        ):
            response = TenantContextMiddleware(lambda req: req.user)(request)

        self.assertIs(response, tenant_user)
        self.assertIs(request.user, tenant_user)
        self.assertEqual(contextos_ao_carregar_sessao, [None])

    @override_settings(
        TENANT_DATABASE_ROUTING_ENABLED=True,
        TENANT_PUBLIC_LINK_ROUTING_READY=True,
        TENANT_BACKGROUND_TASKS_READY=True,
    )
    def test_middleware_recupera_sessao_antiga_de_superadmin_central(self):
        self.usuario.is_superuser = True
        self.usuario.is_staff = True
        self.usuario.save(update_fields=['is_superuser', 'is_staff'])
        request = RequestFactory().get('/auth/selecionar-filial/')
        request.session = {'tenant_db_alias': self.banco.db_alias}
        request.user = SimpleLazyObject(lambda: self.usuario)

        with (
            patch(
                'apps.core.middleware.tenant.register_tenant_database',
                return_value=True,
            ),
            patch.object(Usuario.objects, 'using') as using,
        ):
            response = TenantContextMiddleware(lambda req: req.user)(request)

        self.assertIs(response, request.user)
        self.assertEqual(request.user.pk, self.usuario.pk)
        self.assertEqual(request.session['auth_database_alias'], 'default')
        using.assert_not_called()

    def test_selecao_global_consulta_filiais_no_banco_gerencial(self):
        self.usuario.is_superuser = True
        self.usuario.is_staff = True
        self.usuario.save(update_fields=['is_superuser', 'is_staff'])
        request = RequestFactory().get('/auth/selecionar-filial/')
        request.session = {'tenant_db_alias': self.banco.db_alias}
        request.user = self.usuario

        with (
            tenant_db(self.banco.db_alias),
            patch('apps.core.views.auth.render', side_effect=lambda _r, _t, c: c),
        ):
            context = SelecionarFilialView().get(request)

        self.assertEqual(context['filiais']._db, 'default')
        self.assertEqual(context['empresas']._db, 'default')
        self.assertEqual(list(context['filiais']), [self.filial])
        self.assertEqual(list(context['empresas']), [self.empresa])

    def test_central_nao_oferece_empresa_inativa_como_contexto_de_trabalho(self):
        self.usuario.is_superuser = True
        self.usuario.is_staff = True
        self.usuario.save(update_fields=['is_superuser', 'is_staff'])
        inativa = Empresa.objects.create(
            razao_social='Empresa antiga',
            nome_fantasia='Empresa repetida',
            cnpj='88777666000155',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
            ativo=False,
        )
        request = RequestFactory().get('/gestao/central/')
        request.user = self.usuario

        with patch(
            'apps.core.views.admin_area.render', side_effect=lambda _r, _t, c: c,
        ):
            context = central_administrativa(request)

        self.assertEqual(list(context['empresas']), [self.empresa])
        self.assertNotIn(inativa, context['empresas'])
        self.assertEqual(context['total_empresas'], 1)

    @override_settings(TENANT_DATABASE_ROUTING_ENABLED=True)
    def test_troca_global_falha_sem_misturar_tenant_novo_com_filial_antiga(self):
        self.usuario.is_superuser = True
        self.usuario.is_staff = True
        self.usuario.save(update_fields=['is_superuser', 'is_staff'])
        request = RequestFactory().get(f'/auth/trocar-filial/{self.filial.pk}/')
        request.user = self.usuario
        request.tenant_db_alias = 'empresa_anterior'
        request.session = {
            'auth_database_alias': 'default',
            'tenant_db_alias': 'empresa_anterior',
            'filial_ativa_id': 999,
        }
        default_manager = Filial.objects.using('default')
        tenant_manager = Mock()
        tenant_manager.get.side_effect = Filial.DoesNotExist

        with (
            patch(
                'apps.core.services.auth_service.register_tenant_database',
                return_value=True,
            ),
            patch.object(
                Filial.objects,
                'using',
                side_effect=lambda alias: (
                    default_manager if alias == 'default' else tenant_manager
                ),
            ),
        ):
            with self.assertRaises(DadosInvalidosError):
                AuthService.trocar_filial(request, self.filial.pk)

        self.assertEqual(request.session['tenant_db_alias'], 'empresa_anterior')
        self.assertEqual(request.session['filial_ativa_id'], 999)

    @override_settings(
        TENANT_DATABASE_ROUTING_ENABLED=True,
        TENANT_PUBLIC_LINK_ROUTING_READY=True,
        TENANT_BACKGROUND_TASKS_READY=True,
    )
    def test_rota_central_preserva_alias_sem_ativar_contexto(self):
        request = RequestFactory().get('/admin/')
        request.session = {'tenant_db_alias': self.banco.db_alias}
        request.user = self.usuario

        response = TenantContextMiddleware(
            lambda req: get_current_tenant_db(),
        )(request)

        self.assertIsNone(response)
        self.assertEqual(request.session['tenant_db_alias'], self.banco.db_alias)
        self.assertEqual(request.selected_tenant_db_alias, self.banco.db_alias)

    @override_settings(
        TENANT_DATABASE_ROUTING_ENABLED=True,
        TENANT_PUBLIC_LINK_ROUTING_READY=True,
        TENANT_BACKGROUND_TASKS_READY=True,
    )
    def test_gestao_preserva_qual_tenant_estava_selecionado(self):
        request = RequestFactory().get('/gestao/usuarios/')
        request.session = {
            'tenant_db_alias': self.banco.db_alias,
            'filial_ativa_id': 1,
        }
        request.user = self.usuario

        response = TenantContextMiddleware(
            lambda req: (
                req.tenant_db_alias,
                req.selected_tenant_db_alias,
            ),
        )(request)

        self.assertEqual(response, (None, self.banco.db_alias))

    @patch('apps.core.middleware.filial.register_tenant_database', return_value=True)
    def test_filial_central_e_mapeada_por_empresa_e_cnpj(self, _register):
        request = SimpleNamespace(
            selected_tenant_db_alias=self.banco.db_alias,
            tenant_db_alias=None,
        )
        tenant_filial = SimpleNamespace(cnpj=self.filial.cnpj)
        tenant_manager = Mock()
        tenant_manager.get.return_value = tenant_filial
        central_manager = Mock()
        central_manager.select_related.return_value.get.return_value = self.filial
        banco_manager = Mock()
        banco_manager.get.return_value = self.banco

        with (
            patch.object(EmpresaBanco.objects, 'using', return_value=banco_manager),
            patch.object(
                Filial.objects,
                'using',
                side_effect=lambda alias: (
                    tenant_manager if alias == self.banco.db_alias else central_manager
                ),
            ),
        ):
            filial, tentou_mapear = FilialMiddleware._filial_central_do_tenant(
                request, 1,
            )

        self.assertTrue(tentou_mapear)
        self.assertEqual(filial, self.filial)
        central_manager.select_related.return_value.get.assert_called_once_with(
            empresa_id=self.empresa.pk,
            cnpj=self.filial.cnpj,
            ativo=True,
        )

    def test_usuarios_e_perfis_usam_filial_central_mapeada(self):
        user = Mock(is_authenticated=True, is_superuser=True, filial_id=None)
        user.pode_acessar_filial.return_value = True
        user.perfil_para_filial.return_value = self.perfil

        for path in ('/gestao/usuarios/', '/gestao/perfis/'):
            with self.subTest(path=path):
                request = RequestFactory().get(path)
                request.session = {
                    'tenant_db_alias': self.banco.db_alias,
                    'filial_ativa_id': 1,
                }
                request.user = user
                request.tenant_db_alias = None
                request.selected_tenant_db_alias = self.banco.db_alias

                with patch.object(
                    FilialMiddleware,
                    '_filial_central_do_tenant',
                    return_value=(self.filial, True),
                ):
                    response = FilialMiddleware(lambda req: req.filial_ativa)(request)

                self.assertEqual(response, self.filial)

    def test_falha_no_mapeamento_nao_cai_em_filial_de_mesmo_id(self):
        request = RequestFactory().get('/gestao/usuarios/')
        request.session = {
            'tenant_db_alias': self.banco.db_alias,
            'filial_ativa_id': self.filial.pk,
        }
        request.user = Mock(is_authenticated=True, is_superuser=True, filial_id=None)
        request.tenant_db_alias = None
        request.selected_tenant_db_alias = self.banco.db_alias

        with patch.object(
            FilialMiddleware,
            '_filial_central_do_tenant',
            return_value=(None, True),
        ):
            response = FilialMiddleware(lambda _request: None)(request)

        self.assertEqual(response.status_code, 302)
        self.assertNotIn('filial_ativa_id', request.session)

    @override_settings(TENANT_DATABASE_ROUTING_ENABLED=True)
    def test_login_tenant_mantem_sessao_no_usuario_do_diretorio_central(self):
        request = RequestFactory().post('/auth/login/')
        request.session = {}
        request.tenant_db_alias = None
        tenant_user = Mock(
            pk=101,
            email='tenant@example.com',
            ativo=True,
            bloqueado_ate=None,
            filial=None,
            filial_id=None,
            is_superuser=False,
        )
        central_user = Mock(pk=999, email=tenant_user.email, is_superuser=False)
        default_manager = Mock()
        default_manager.get.return_value = central_user

        with (
            patch.object(
                AuthService,
                '_resolver_tenant_por_email',
                return_value=self.banco.db_alias,
            ),
            patch('apps.core.services.auth_service.authenticate', return_value=tenant_user),
            patch('apps.core.services.auth_service.get_client_ip', return_value='127.0.0.1'),
            patch.object(Usuario.objects, 'using') as using,
            patch('apps.core.services.auth_service.LogAcesso.objects') as logs,
        ):
            using.side_effect = lambda alias: default_manager if alias == 'default' else Mock()
            user = AuthService.login(request, tenant_user.email, 'senha')

        self.assertIs(user, central_user)
        self.assertIs(request._tenant_authenticated_user, tenant_user)
        self.assertEqual(request.session['tenant_db_alias'], self.banco.db_alias)
        logs.using.assert_called_once_with(self.banco.db_alias)

    @override_settings(TENANT_DATABASE_ROUTING_ENABLED=True)
    def test_nova_empresa_recebe_admin_local_e_nao_superusuario_global(self):
        with patch.dict(os.environ, {'ADMIN_SENHA': 'Senha-Forte-9876'}):
            call_command(
                'criar_empresa_admin',
                cnpj='11222333000181',
                razao_social='Empresa Nova LTDA',
                nome_fantasia='Empresa Nova',
                uf='RN',
                cidade='Natal',
                admin_email='admin-nova@example.com',
                admin_nome='Admin Nova',
                stdout=StringIO(),
            )

        usuario = Usuario.objects.get(email='admin-nova@example.com')
        self.assertTrue(usuario.perfil.is_admin)
        self.assertFalse(usuario.is_superuser)
        self.assertFalse(usuario.is_staff)

    @override_settings(
        RAILWAY_PROJECT_ID='project-stage',
        RAILWAY_ENVIRONMENT_ID='environment-stage',
    )
    def test_volume_railway_e_identificado_pelo_servico_e_nao_pelo_nome(self):
        payload = {
            'environment': {
                'volumeInstances': {
                    'edges': [
                        {'node': {
                            'serviceId': 'outro-servico',
                            'volume': {'id': 'volume-1', 'name': 'postgres-volume'},
                        }},
                        {'node': {
                            'serviceId': 'tenant-service-id',
                            'volume': {'id': 'volume-2', 'name': 'nome-aleatorio-rnBF'},
                        }},
                    ],
                },
            },
        }
        with patch.object(RailwayProvisioner, '_graphql', return_value=payload) as graphql:
            volume = RailwayProvisioner._find_volume('tenant-service-id')

        self.assertEqual(volume['id'], 'volume-2')
        graphql.assert_called_once()
