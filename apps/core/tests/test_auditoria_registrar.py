"""
`registrar_auditoria` grava `usuario`/`filial` -- até este fix, atribuindo
a INSTÂNCIA (`usuario=usuario_obj`) em vez do id. Com o roteamento por
tenant ativo, `usuario` quase sempre chega como `request.user` (um
`SimpleLazyObject`), que pode resolver num alias de banco diferente do
de `RegistroAuditoria` -- e a instância dispara a checagem
`allow_relation` de `apps/core/db_router.py`, que recusa a relação
quando os dois lados não batem ("the current database router prevents
this relation"). Um usuário relatou exatamente isso tentando excluir um
título a pagar: a exclusão em si já usava `_id`, mas o registro de
auditoria que vinha logo depois ainda atribuía a instância.

Estes testes cobrem que `registrar_auditoria` grava certo usando só o
id -- não reproduzem o erro de roteamento em si (exigiria bancos
tenant de verdade), mas garantem que a gravação não regride pro
padrão antigo.
"""
from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError
from django.test import RequestFactory, TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, RegistroAuditoria, Usuario
from apps.core.services.auditoria import registrar_auditoria
from apps.core.tenant_context import tenant_atomic


class RegistrarAuditoriaBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Auditoria LTDA', nome_fantasia='Auditoria',
            cnpj='73345678000101', regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Empresa Auditoria LTDA',
            cnpj='73345678000282', uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email='auditoria-registrar@teste.local', nome='Auditoria', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def _objeto_qualquer(self):
        # Qualquer model com `pk` serve -- a filial já está à mão.
        return self.filial


class RegistrarAuditoriaTests(RegistrarAuditoriaBase):

    def test_grava_usuario_e_filial_a_partir_do_request(self):
        request = RequestFactory().post('/qualquer/')
        request.user = self.usuario
        request.filial_ativa = self.filial

        registro = registrar_auditoria(
            request=request,
            modulo=RegistroAuditoria.Modulo.FINANCEIRO,
            acao=RegistroAuditoria.Acao.EXCLUIR,
            objeto=self._objeto_qualquer(),
            descricao='Teste de auditoria',
            justificativa='Motivo qualquer.',
        )

        self.assertIsNotNone(registro)
        self.assertEqual(registro.usuario_id, self.usuario.pk)
        self.assertEqual(registro.filial_id, self.filial.pk)

    def test_usuario_explicito_prevalece_sobre_o_do_request(self):
        outro_usuario = Usuario.objects.create_user(
            email='auditoria-outro@teste.local', nome='Outro', password='x' * 12,
            empresa=self.empresa, perfil=self.usuario.perfil, filial=self.filial,
        )
        request = RequestFactory().post('/qualquer/')
        request.user = self.usuario
        request.filial_ativa = self.filial

        registro = registrar_auditoria(
            request=request,
            usuario=outro_usuario,
            modulo=RegistroAuditoria.Modulo.FINANCEIRO,
            acao=RegistroAuditoria.Acao.EXCLUIR,
            objeto=self._objeto_qualquer(),
        )

        self.assertEqual(registro.usuario_id, outro_usuario.pk)

    def test_usuario_anonimo_grava_sem_usuario(self):
        from django.contrib.auth.models import AnonymousUser

        request = RequestFactory().post('/qualquer/')
        request.user = AnonymousUser()
        request.filial_ativa = self.filial

        registro = registrar_auditoria(
            request=request,
            modulo=RegistroAuditoria.Modulo.FINANCEIRO,
            acao=RegistroAuditoria.Acao.EXCLUIR,
            objeto=self._objeto_qualquer(),
        )

        self.assertIsNone(registro.usuario_id)

    def test_sem_objeto_com_pk_nao_grava_nada(self):
        request = RequestFactory().post('/qualquer/')
        request.user = self.usuario
        request.filial_ativa = self.filial

        registro = registrar_auditoria(
            request=request,
            modulo=RegistroAuditoria.Modulo.FINANCEIRO,
            acao=RegistroAuditoria.Acao.EXCLUIR,
            objeto=object(),
        )

        self.assertIsNone(registro)
        self.assertEqual(RegistroAuditoria.objects.count(), 0)


class RegistrarAuditoriaFalhaDeBancoTests(RegistrarAuditoriaBase):
    """
    Em produção, cada empresa tem seu próprio banco. Um operador que atende
    várias empresas pode ter `usuario_id` sem linha correspondente no banco
    de uma delas -- o INSERT do registro de auditoria quebra a foreign key
    de verdade (não é o `allow_relation` do Django, é o Postgres mesmo).
    Isso já derrubou em produção uma tela que não tinha nada a ver com
    auditoria (criar um depósito) com 500, depois de a ação principal já
    ter sido salva -- auditoria é bookkeeping, não pode travar o trabalho.
    """

    def test_nao_devolve_excecao_quando_o_insert_falha(self):
        request = RequestFactory().post('/qualquer/')
        request.user = self.usuario
        request.filial_ativa = self.filial

        with patch(
            'apps.core.services.auditoria.RegistroAuditoria.objects.create',
            side_effect=IntegrityError('usuario_id nao existe neste banco'),
        ):
            registro = registrar_auditoria(
                request=request,
                modulo=RegistroAuditoria.Modulo.ESTOQUE,
                acao=RegistroAuditoria.Acao.CRIAR,
                objeto=self._objeto_qualquer(),
            )

        self.assertIsNone(registro)

    def test_falha_na_auditoria_nao_derruba_a_transacao_de_quem_chamou(self):
        """A auditoria roda num savepoint -- se ela falhar, o resto do
        trabalho feito na MESMA transação continua de pé."""
        request = RequestFactory().post('/qualquer/')
        request.user = self.usuario
        request.filial_ativa = self.filial

        with tenant_atomic():
            with patch(
                'apps.core.services.auditoria.RegistroAuditoria.objects.create',
                side_effect=IntegrityError('usuario_id nao existe neste banco'),
            ):
                registrar_auditoria(
                    request=request,
                    modulo=RegistroAuditoria.Modulo.ESTOQUE,
                    acao=RegistroAuditoria.Acao.CRIAR,
                    objeto=self._objeto_qualquer(),
                )
            # Sem o savepoint isolando a falha, esta escrita levantaria
            # TransactionManagementError -- a transação já estaria abortada.
            self.usuario.nome = 'Renomeado depois da falha de auditoria'
            self.usuario.save(update_fields=['nome'])

        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.nome, 'Renomeado depois da falha de auditoria')
