import json
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.pdv.models import EventoInstalacaoPDVOffline, InstalacaoPDVOffline


class InstalacaoOfflineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Empresa Offline LTDA",
            nome_fantasia="Empresa Offline",
            cnpj="71345678000191",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social="Filial Offline",
            nome_fantasia="Loja Offline",
            cnpj="71345678000192",
            uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome="Administrador Offline", is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email="operador-offline@example.com", nome="Operador Offline",
            password="teste1234", empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.superuser = Usuario.objects.create_superuser(
            email="suporte-offline@example.com", nome="Suporte Offline",
            password="teste1234", empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session["filial_ativa_id"] = self.filial.pk
        session.save()
        self.installation_id = "inst" + "a" * 32

    def post_api(self, action, **extra):
        return self.client.post(
            reverse("pdv:api_instalacao_offline"),
            data=json.dumps({
                "action": action,
                "installation_id": self.installation_id,
                "nome_dispositivo": "Caixa Frente 01",
                **extra,
            }),
            content_type="application/json",
        )

    def test_ativa_e_consulta_sem_receber_segredos(self):
        response = self.post_api("activate", recuperacao_configurada=True)

        self.assertEqual(response.status_code, 200)
        instalacao = InstalacaoPDVOffline.objects.get()
        self.assertEqual(instalacao.status, InstalacaoPDVOffline.Status.ATIVA)
        self.assertEqual(instalacao.nome_dispositivo, "Caixa Frente 01")
        self.assertTrue(instalacao.recuperacao_configurada)
        self.assertFalse(hasattr(instalacao, "pin"))
        self.assertFalse(hasattr(instalacao, "codigo_recuperacao"))
        self.assertTrue(EventoInstalacaoPDVOffline.objects.filter(tipo="ativada").exists())

        status = self.post_api("status", recuperacao_configurada=True)
        self.assertEqual(status.json()["status"], "ativa")

    def test_heartbeat_atualiza_monitoramento_sem_receber_conteudo_da_venda(self):
        self.post_api("activate", recuperacao_configurada=True)
        catalogo_em = timezone.now() - timedelta(hours=2)
        ultima_sync = timezone.now() - timedelta(minutes=3)
        ultimo_backup = timezone.now() - timedelta(minutes=1)

        response = self.post_api(
            "status",
            recuperacao_configurada=True,
            fila_pendente_quantidade=3,
            fila_erro_quantidade=1,
            catalogo_atualizado_em=catalogo_em.isoformat(),
            ultima_sincronizacao_em=ultima_sync.isoformat(),
            ultimo_backup_em=ultimo_backup.isoformat(),
            ultimo_erro_sincronizacao="Estoque insuficiente",
        )

        self.assertEqual(response.status_code, 200)
        instalacao = InstalacaoPDVOffline.objects.get()
        self.assertEqual(instalacao.fila_pendente_quantidade, 3)
        self.assertEqual(instalacao.fila_erro_quantidade, 1)
        self.assertEqual(instalacao.ultimo_erro_sincronizacao, "Estoque insuficiente")
        self.assertAlmostEqual(instalacao.catalogo_atualizado_em, catalogo_em, delta=timedelta(seconds=1))
        self.assertAlmostEqual(instalacao.ultima_sincronizacao_em, ultima_sync, delta=timedelta(seconds=1))
        self.assertAlmostEqual(instalacao.ultimo_backup_em, ultimo_backup, delta=timedelta(seconds=1))

        self.client.force_login(self.superuser)
        painel = self.client.get(reverse("core:admin_instalacoes_pdv_offline"), {"pendencias": "1"})
        self.assertContains(painel, "3 aguardando")
        self.assertContains(painel, "1 com erro")
        self.assertContains(painel, "Estoque insuficiente")

    def test_heartbeat_registra_pdv_sem_ativar_protecao_offline(self):
        response = self.post_api(
            "heartbeat",
            fila_pendente_quantidade=2,
            ultimo_erro_sincronizacao="Sem conexão.",
        )

        self.assertEqual(response.status_code, 200)
        instalacao = InstalacaoPDVOffline.objects.get()
        self.assertEqual(instalacao.status, InstalacaoPDVOffline.Status.INATIVA)
        self.assertFalse(instalacao.recuperacao_configurada)
        self.assertEqual(instalacao.fila_pendente_quantidade, 2)
        self.assertEqual(instalacao.ultimo_erro_sincronizacao, "Sem conexão.")
        self.assertFalse(EventoInstalacaoPDVOffline.objects.exists())

    def test_superusuario_libera_novo_pin_e_operador_reativa(self):
        self.post_api("activate", recuperacao_configurada=True)
        instalacao = InstalacaoPDVOffline.objects.get()
        revisao_inicial = instalacao.revisao

        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse("core:admin_instalacao_pdv_offline_acao", args=[instalacao.pk]),
            {"acao": "redefinir", "motivo": "Operador esqueceu o PIN"},
        )
        self.assertRedirects(response, reverse("core:admin_instalacoes_pdv_offline"))
        instalacao.refresh_from_db()
        self.assertEqual(instalacao.status, InstalacaoPDVOffline.Status.REDEFINIR)
        self.assertGreater(instalacao.revisao, revisao_inicial)

        self.client.force_login(self.usuario)
        status = self.post_api("status", recuperacao_configurada=True)
        self.assertEqual(status.json()["status"], "redefinir")
        reativada = self.post_api("activate", recuperacao_configurada=True)
        self.assertEqual(reativada.status_code, 200)
        instalacao.refresh_from_db()
        self.assertEqual(instalacao.status, InstalacaoPDVOffline.Status.ATIVA)

    def test_revogacao_bloqueia_reativacao_ate_liberacao(self):
        self.post_api("activate", recuperacao_configurada=True)
        instalacao = InstalacaoPDVOffline.objects.get()
        self.client.force_login(self.superuser)
        self.client.post(
            reverse("core:admin_instalacao_pdv_offline_acao", args=[instalacao.pk]),
            {"acao": "revogar", "motivo": "Computador perdido"},
        )

        self.client.force_login(self.usuario)
        response = self.post_api("activate", recuperacao_configurada=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["status"], "revogada")
