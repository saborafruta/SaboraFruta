from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import (
    Empresa,
    EmpresaBanco,
    Filial,
    LogAcesso,
    PerfilAcesso,
    Permissao,
    PoliticaReplicacao,
    PoliticaReplicacaoFilial,
    RegistroAuditoria,
    SeparacaoFilial,
    UsuarioFilialAcesso,
)
from apps.core.services.separacao_filial_service import SeparacaoFilialService, SeparacaoFilialError
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial
from apps.estoque.models import MovimentacaoEstoque


class SeparacaoFilialServiceTest(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            razao_social='Grupo Demo LTDA',
            nome_fantasia='Grupo Demo',
            cnpj='11111111000111',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
            cidade='Natal',
            uf='RN',
        )
        self.filial_matriz = Filial.objects.create(
            empresa=self.empresa,
            razao_social='Filial Matriz LTDA',
            nome_fantasia='Matriz',
            cnpj='11111111000200',
            is_matriz=True,
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
            cidade='Natal',
            uf='RN',
        )
        self.filial_separada = Filial.objects.create(
            empresa=self.empresa,
            razao_social='Filial Separada LTDA',
            nome_fantasia='Separada',
            cnpj='22222222000122',
            is_matriz=False,
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
            cidade='Mossoro',
            uf='RN',
        )
        self.perfil = PerfilAcesso.objects.create(
            empresa=self.empresa,
            nome='Administrador',
            is_admin=True,
        )
        Permissao.objects.create(
            perfil=self.perfil,
            modulo=Permissao.Modulo.CONFIG,
            pode_ver=True,
            pode_criar=True,
            pode_editar=True,
        )
        usuario_model = get_user_model()
        self.admin = usuario_model.objects.create_superuser(
            email='admin@teste.com',
            password='123456',
            nome='Admin',
            empresa=self.empresa,
            perfil=self.perfil,
        )

    def criar_usuario_exclusivo(self):
        usuario_model = get_user_model()
        usuario = usuario_model.objects.create_user(
            email='usuario@teste.com',
            password='123456',
            nome='Usuario',
            empresa=self.empresa,
            perfil=self.perfil,
            filial=self.filial_separada,
        )
        UsuarioFilialAcesso.objects.create(
            usuario=usuario,
            filial=self.filial_separada,
            perfil=self.perfil,
            ativo=True,
            is_padrao=True,
        )
        return usuario

    def test_simulacao_sem_bloqueios_para_usuario_exclusivo(self):
        self.criar_usuario_exclusivo()
        processo = SeparacaoFilialService.obter_ou_criar(self.filial_separada, self.admin)

        simulacao = SeparacaoFilialService.simular(processo)

        self.assertEqual(simulacao['bloqueios'], [])
        self.assertEqual(processo.status, SeparacaoFilial.Status.SIMULADO)
        self.assertEqual(simulacao['usuarios']['exclusivos'][0]['email'], 'usuario@teste.com')

    def test_execucao_move_filial_usuario_e_cria_banco(self):
        usuario = self.criar_usuario_exclusivo()
        processo = SeparacaoFilialService.obter_ou_criar(self.filial_separada, self.admin)

        with TemporaryDirectory() as tmpdir, override_settings(
            TENANT_DATABASE_PROVISIONING_MODE='manual',
            MEDIA_ROOT=Path(tmpdir),
        ):
            processo = SeparacaoFilialService.executar(processo, self.admin)

            self.filial_separada.refresh_from_db()
            usuario.refresh_from_db()
            processo.refresh_from_db()
            banco = EmpresaBanco.objects.get(empresa=processo.empresa_destino)

            self.assertTrue(default_storage.exists(processo.backup_path))
            self.assertTrue(default_storage.exists(processo.backup_exportacao_path))

        self.assertEqual(processo.status, SeparacaoFilial.Status.CONCLUIDO)
        self.assertEqual(processo.progresso_percentual, 100)
        self.assertTrue(processo.relatorio_json)
        self.assertEqual(processo.rollback_json.get('tipo'), 'assistido')
        self.assertEqual(self.filial_separada.empresa_id, processo.empresa_destino_id)
        self.assertTrue(self.filial_separada.is_matriz)
        self.assertTrue(Filial.objects.get(pk=self.filial_matriz.pk).is_matriz)
        self.assertEqual(usuario.empresa_id, processo.empresa_destino_id)
        self.assertEqual(usuario.filial_id, self.filial_separada.pk)
        self.assertEqual(banco.status, EmpresaBanco.Status.AGUARDANDO_CONFIGURACAO)
        self.assertTrue(
            RegistroAuditoria.objects.filter(
                filial=self.filial_separada,
                acao=RegistroAuditoria.Acao.TRANSFERIR,
                objeto_id=self.filial_separada.pk,
            ).exists()
        )

    def test_usuario_multifilial_fica_para_revisao_sem_perder_outros_acessos(self):
        usuario = self.criar_usuario_exclusivo()
        acesso_matriz = UsuarioFilialAcesso.objects.create(
            usuario=usuario,
            filial=self.filial_matriz,
            perfil=self.perfil,
            ativo=True,
        )
        processo = SeparacaoFilialService.obter_ou_criar(self.filial_separada, self.admin)

        simulacao = SeparacaoFilialService.simular(processo)

        self.assertEqual(simulacao['bloqueios'], [])
        self.assertTrue(any('revisão manual' in aviso for aviso in simulacao['avisos']))

        with TemporaryDirectory() as tmpdir, override_settings(
            TENANT_DATABASE_PROVISIONING_MODE='manual',
            MEDIA_ROOT=Path(tmpdir),
        ):
            processo = SeparacaoFilialService.executar(processo, self.admin)

        usuario.refresh_from_db()
        acesso_matriz.refresh_from_db()
        acesso_separado = UsuarioFilialAcesso.objects.get(
            usuario=usuario,
            filial=self.filial_separada,
        )
        self.assertFalse(acesso_separado.ativo)
        self.assertTrue(acesso_matriz.ativo)
        self.assertEqual(usuario.empresa_id, self.empresa.pk)
        self.assertEqual(usuario.filial_id, self.filial_matriz.pk)
        self.assertEqual(
            processo.relatorio_json['usuarios_revisao_manual'][0]['email'],
            usuario.email,
        )
        acesso_mantido = processo.relatorio_json['usuarios_revisao_manual'][0]['acessos_mantidos'][0]
        self.assertEqual(acesso_mantido['filial'], self.filial_matriz.nome_fantasia)
        self.assertEqual(acesso_mantido['perfil'], self.perfil.nome)

    def test_separacao_desativa_replicacao_ate_revisao(self):
        PoliticaReplicacao.objects.create(
            empresa=self.empresa,
            replicar_clientes=True,
            replicar_produtos_basicos=True,
        )
        PoliticaReplicacaoFilial.objects.create(
            filial=self.filial_separada,
            replicar_clientes=True,
            replicar_produtos_basicos=True,
        )
        self.criar_usuario_exclusivo()
        processo = SeparacaoFilialService.obter_ou_criar(self.filial_separada, self.admin)

        with TemporaryDirectory() as tmpdir, override_settings(
            TENANT_DATABASE_PROVISIONING_MODE='manual',
            MEDIA_ROOT=Path(tmpdir),
        ):
            processo = SeparacaoFilialService.executar(processo, self.admin)

        politica_filial = PoliticaReplicacaoFilial.objects.get(filial=self.filial_separada)
        politica_empresa = PoliticaReplicacao.objects.get(empresa=processo.empresa_destino)
        self.assertFalse(politica_filial.ativo)
        self.assertFalse(politica_filial.replicar_clientes)
        self.assertFalse(politica_filial.replicar_produtos_basicos)
        self.assertFalse(politica_empresa.ativo)
        self.filial_separada.refresh_from_db()
        self.assertFalse(self.filial_separada.participa_replicacao)
        self.assertTrue(SeparacaoFilialService.replicacao_bloqueada(self.filial_separada))

    def test_filial_separada_nao_pode_reativar_replicacao(self):
        destino = Empresa.objects.create(
            razao_social=self.filial_separada.razao_social,
            nome_fantasia=self.filial_separada.nome_fantasia,
            cnpj=self.filial_separada.cnpj,
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        self.filial_separada.empresa = destino
        self.filial_separada.participa_replicacao = False
        self.filial_separada.save(update_fields=['empresa', 'participa_replicacao'])
        SeparacaoFilial.objects.create(
            filial_origem=self.filial_separada,
            empresa_origem=self.empresa,
            empresa_destino=destino,
            status=SeparacaoFilial.Status.CONCLUIDO,
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('core:admin_politica_replicacao_update', args=[destino.pk]),
            {'filial_id': self.filial_separada.pk, 'participa_replicacao': 'on'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.filial_separada.refresh_from_db()
        self.assertFalse(self.filial_separada.participa_replicacao)
        self.assertContains(response, 'nao pode reativar replicacao automaticamente')

    def test_selecao_inclui_produto_compartilhado_e_suas_dependencias(self):
        unidade = UnidadeMedida.objects.create(
            empresa=self.empresa,
            sigla='UN',
            descricao='Unidade',
        )
        UnidadeMedidaFilial.objects.create(unidade=unidade, filial=self.filial_separada)
        produto = Produto.objects.create(
            filial=self.filial_matriz,
            unidade_medida=unidade,
            descricao='Produto compartilhado',
            ncm='20089900',
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial_separada)

        modelos = SeparacaoFilialService._modelos_para_copia_operacional()
        selecionados = SeparacaoFilialService._selecionar_pks_copia(
            modelos,
            'default',
            self.filial_separada.pk,
        )

        self.assertIn(produto.pk, selecionados[Produto._meta.label_lower])
        self.assertIn(unidade.pk, selecionados[UnidadeMedida._meta.label_lower])
        destino = SimpleNamespace(pk=self.filial_separada.pk, empresa_id=999)
        produto_data, remapeado = SeparacaoFilialService._dados_objeto_separado(
            produto,
            destino,
            self.empresa.pk,
        )
        unidade_data, _ = SeparacaoFilialService._dados_objeto_separado(
            unidade,
            destino,
            self.empresa.pk,
        )
        self.assertTrue(remapeado)
        self.assertEqual(produto_data['filial_id'], self.filial_separada.pk)
        self.assertEqual(produto_data['id_externo'], produto.id_externo)
        self.assertEqual(unidade_data['empresa_id'], 999)

    def test_produtos_recebem_identidade_global_distinta(self):
        unidade = UnidadeMedida.objects.create(
            empresa=self.empresa,
            sigla='CX',
            descricao='Caixa',
        )
        produto_a = Produto.objects.create(
            filial=self.filial_matriz,
            unidade_medida=unidade,
            descricao='Produto A',
            ncm='20089900',
        )
        produto_b = Produto.objects.create(
            filial=self.filial_separada,
            unidade_medida=unidade,
            descricao='Produto B',
            ncm='20089900',
        )

        self.assertNotEqual(produto_a.codigo_replicacao, produto_b.codigo_replicacao)

    def test_copia_nao_inclui_sessoes_favoritos_ou_cache(self):
        labels = {
            model._meta.label_lower
            for model in SeparacaoFilialService._modelos_para_copia_operacional()
        }

        self.assertNotIn('core.sessaousuario', labels)
        self.assertNotIn('core.filialfavorita', labels)
        self.assertNotIn('pdv.pdvcache', labels)

    def test_selecao_identifica_usuario_referenciado_por_log(self):
        LogAcesso.objects.create(
            usuario=self.admin,
            filial=self.filial_separada,
            tipo=LogAcesso.Tipo.LOGIN,
            sucesso=True,
        )
        modelos = SeparacaoFilialService._modelos_para_copia_operacional()
        selecionados = SeparacaoFilialService._selecionar_pks_copia(
            modelos,
            'default',
            self.filial_separada.pk,
        )

        ids = SeparacaoFilialService._ids_usuarios_referenciados(
            modelos,
            selecionados,
            'default',
        )

        self.assertIn(self.admin.pk, ids)

    def test_referencia_a_outra_filial_nao_vira_auto_transferencia(self):
        movimentacao = MovimentacaoEstoque(
            filial=self.filial_separada,
            filial_destino=self.filial_matriz,
            produto_id=999,
            usuario=self.admin,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
            quantidade=1,
            quantidade_anterior=1,
            quantidade_posterior=0,
        )

        data, remapeado = SeparacaoFilialService._dados_objeto_separado(
            movimentacao,
            self.filial_separada,
            self.empresa.pk,
        )

        self.assertTrue(remapeado)
        self.assertIsNone(data['filial_destino_id'])

    @patch('apps.core.services.separacao_filial_service.register_tenant_database', return_value=True)
    def test_copia_bloqueia_aliases_apontando_para_o_mesmo_banco(self, _register):
        origem = SimpleNamespace(pk=10, db_alias='default')
        destino = SimpleNamespace(pk=11, db_alias='default')

        resultado = SeparacaoFilialService._copiar_dados_filial_tenant(
            origem,
            destino,
            self.filial_separada,
            empresa_origem=self.empresa,
        )

        self.assertFalse(resultado['executado'])
        self.assertIn('mesma instancia fisica', resultado['motivo'])

    def test_bloqueia_empresa_com_uma_unica_filial_ativa(self):
        self.filial_matriz.ativo = False
        self.filial_matriz.save(update_fields=['ativo'])
        processo = SeparacaoFilialService.obter_ou_criar(self.filial_separada, self.admin)

        simulacao = SeparacaoFilialService.simular(processo)

        self.assertIn(
            'A empresa atual precisa ter pelo menos duas filiais ativas para separar uma delas.',
            simulacao['bloqueios'],
        )

    def test_tela_de_separacao_renderiza_para_superadmin(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse('core:admin_filial_separar', args=[self.filial_separada.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Separar filial')
        self.assertContains(response, 'Banco novo previsto')

    def test_tela_de_progresso_renderiza_para_superadmin(self):
        self.client.force_login(self.admin)
        processo = SeparacaoFilialService.obter_ou_criar(self.filial_separada, self.admin)
        SeparacaoFilialService.confirmar_execucao(processo, self.admin)

        response = self.client.get(reverse('core:admin_filial_separar_progress', args=[processo.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Transformando filial em empresa independente')

    def test_status_da_separacao_retorna_payload(self):
        self.client.force_login(self.admin)
        processo = SeparacaoFilialService.obter_ou_criar(self.filial_separada, self.admin)

        response = self.client.get(reverse('core:admin_filial_separar_status', args=[processo.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn('progress', response.json())

    def test_inicio_da_separacao_dispara_task_sem_executar_na_requisicao(self):
        self.criar_usuario_exclusivo()
        self.client.force_login(self.admin)
        processo = SeparacaoFilialService.obter_ou_criar(self.filial_separada, self.admin)
        SeparacaoFilialService.confirmar_execucao(processo, self.admin)

        with patch('apps.core.tasks.executar_separacao_filial.apply_async', return_value=SimpleNamespace(id='task-123')) as mocked:
            response = self.client.post(reverse('core:admin_filial_separar_start', args=[processo.pk]))

        processo.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(processo.status, SeparacaoFilial.Status.EXECUTANDO)
        self.assertEqual(processo.executor_backend, 'celery')
        self.assertEqual(processo.task_id, 'task-123')
        self.assertIsNone(processo.empresa_destino_id)
        mocked.assert_called_once()

    def test_erro_interrompido_pode_ser_retomado_sem_bloqueio_de_cnpj(self):
        self.criar_usuario_exclusivo()
        processo = SeparacaoFilialService.obter_ou_criar(self.filial_separada, self.admin)
        simulacao = SeparacaoFilialService.simular(processo)
        destino = Empresa.objects.create(
            razao_social=self.filial_separada.razao_social,
            nome_fantasia=self.filial_separada.nome_fantasia,
            cnpj=self.filial_separada.cnpj,
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        self.filial_separada.empresa = destino
        self.filial_separada.save(update_fields=['empresa'])
        processo.empresa_destino = destino
        processo.simulacao_json = simulacao
        processo.status = SeparacaoFilial.Status.ERRO
        processo.save(update_fields=['empresa_destino', 'simulacao_json', 'status'])

        retomada = SeparacaoFilialService.simular(processo)

        self.assertEqual(retomada['bloqueios'], [])
        self.assertTrue(any('Retomando' in aviso for aviso in retomada['avisos']))
