"""
A lista de Manifestos de Carga: botões de editar e excluir.

A coluna Ações não existia -- só dava pra ver o manifesto (pelo número) e
não tinha jeito nenhum de editar ou apagar um manifesto criado errado sem
entrar no detalhe.
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.logistica.models import ManifestoCarga


class ListaDeManifestosTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Carga LTDA', nome_fantasia='Carga',
            cnpj='83345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='83345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='manifesto-acoes@doca.local', nome='Carga', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse('logistica:manifesto-list')

    def _manifesto(self, numero=1, status=ManifestoCarga.Status.RASCUNHO):
        return ManifestoCarga.objects.create(
            filial=self.filial, numero=numero, data_emissao=timezone.localdate(),
            status=status, veiculo_placa='ABC1D23',
        )

    def test_lista_mostra_o_link_de_editar(self):
        manifesto = self._manifesto()

        html = self.client.get(self.url).content.decode()

        self.assertIn(reverse('logistica:manifesto-update', args=[manifesto.pk]), html)

    def test_lista_mostra_o_botao_de_excluir_para_status_liberado(self):
        manifesto = self._manifesto(status=ManifestoCarga.Status.EMITIDO)

        html = self.client.get(self.url).content.decode()

        self.assertIn(reverse('logistica:manifesto-delete', args=[manifesto.pk]), html)

    def test_lista_esconde_excluir_para_em_transito(self):
        manifesto = self._manifesto(status=ManifestoCarga.Status.EM_TRANSITO)

        html = self.client.get(self.url).content.decode()

        self.assertNotIn(reverse('logistica:manifesto-delete', args=[manifesto.pk]), html)

    def test_lista_esconde_excluir_para_encerrado(self):
        manifesto = self._manifesto(status=ManifestoCarga.Status.ENCERRADO)

        html = self.client.get(self.url).content.decode()

        self.assertNotIn(reverse('logistica:manifesto-delete', args=[manifesto.pk]), html)

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        self._manifesto()

        html = self.client.get(self.url).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, 'vazou sintaxe de template no HTML')


class ExcluirManifestoTests(TestCase):
    """
    Excluir apaga o manifesto (e os documentos junto, via cascata). Em
    trânsito ou encerrado já virou histórico de operação -- aí a saída é
    cancelar, não excluir, mesma regra do Romaneio de Carga.
    """

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Carga LTDA', nome_fantasia='Carga',
            cnpj='93345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='93345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='exclui-manifesto@doca.local', nome='Carga', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def _manifesto(self, numero=1, status=ManifestoCarga.Status.RASCUNHO):
        return ManifestoCarga.objects.create(
            filial=self.filial, numero=numero, data_emissao=timezone.localdate(),
            status=status, veiculo_placa='ABC1D23',
        )

    def test_exclui_manifesto_em_rascunho(self):
        manifesto = self._manifesto(status=ManifestoCarga.Status.RASCUNHO)

        resposta = self.client.post(
            reverse('logistica:manifesto-delete', args=[manifesto.pk])
        )

        self.assertRedirects(resposta, reverse('logistica:manifesto-list'))
        self.assertFalse(ManifestoCarga.objects.filter(pk=manifesto.pk).exists())

    def test_exclui_manifesto_emitido(self):
        manifesto = self._manifesto(status=ManifestoCarga.Status.EMITIDO)

        self.client.post(reverse('logistica:manifesto-delete', args=[manifesto.pk]))

        self.assertFalse(ManifestoCarga.objects.filter(pk=manifesto.pk).exists())

    def test_exclui_manifesto_cancelado(self):
        manifesto = self._manifesto(status=ManifestoCarga.Status.CANCELADO)

        self.client.post(reverse('logistica:manifesto-delete', args=[manifesto.pk]))

        self.assertFalse(ManifestoCarga.objects.filter(pk=manifesto.pk).exists())

    def test_recusa_excluir_manifesto_em_transito(self):
        manifesto = self._manifesto(status=ManifestoCarga.Status.EM_TRANSITO)

        resposta = self.client.post(
            reverse('logistica:manifesto-delete', args=[manifesto.pk])
        )

        self.assertRedirects(resposta, reverse('logistica:manifesto-list'))
        self.assertTrue(ManifestoCarga.objects.filter(pk=manifesto.pk).exists())

    def test_recusa_excluir_manifesto_encerrado(self):
        manifesto = self._manifesto(status=ManifestoCarga.Status.ENCERRADO)

        self.client.post(reverse('logistica:manifesto-delete', args=[manifesto.pk]))

        self.assertTrue(ManifestoCarga.objects.filter(pk=manifesto.pk).exists())

    def test_exclusao_leva_os_documentos_junto(self):
        from apps.logistica.models import DocumentoManifestoCarga

        manifesto = self._manifesto()
        DocumentoManifestoCarga.objects.create(
            manifesto=manifesto, numero_documento='123', peso_kg='10',
        )

        self.client.post(reverse('logistica:manifesto-delete', args=[manifesto.pk]))

        self.assertEqual(DocumentoManifestoCarga.objects.count(), 0)
