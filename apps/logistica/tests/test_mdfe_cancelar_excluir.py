"""
Cancelar e excluir um MDF-e que nunca foi enviado à SEFAZ.

Antes, um manifesto em rascunho não tinha saída nenhuma: "Cancelar MDF-e"
só aparecia depois de autorizado (fala com a Focus NFe), e não existia
exclusão em lugar nenhum. Quem criava um MDF-e por engano ficava com ele
preso na lista para sempre.

O QUE ESTES TESTES CERCAM:

  · CANCELAR e EXCLUIR só valem para rascunho/aguardando_nfe/rejeitado --
    um MDF-e já enviado à SEFAZ tem que passar pelo cancelamento de
    verdade (`mdfe-cancelar-focus`), nunca por aqui;

  · EXCLUIR apaga de vez (e os vínculos com documento, em cascata); a
    NF-e/CT-e em si não é tocada;

  · CANCELAR mantém o registro, só muda o status -- a diferença que
    justifica os dois botões existirem separados.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone


class BaseMDFeCancelarExcluir(TestCase):
    _seq = 0

    def setUp(self):
        from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario

        self.empresa = Empresa.objects.create(
            razao_social='T', cnpj='21222333000181',
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Matriz', nome_fantasia='Matriz',
            cnpj='21222333000181', uf='RN', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=self.empresa, nome='Admin', is_admin=True)
        self.usuario = Usuario.objects.create_user(
            email='u@teste.local', nome='U', password='senha-de-teste-123',
            empresa=self.empresa, perfil=perfil, filial=self.filial)
        self.client.force_login(self.usuario)

    def _mdfe(self, status='rascunho', **kw):
        from apps.logistica.models import MDFe

        BaseMDFeCancelarExcluir._seq += 1
        dados = dict(
            filial=self.filial, numero=BaseMDFeCancelarExcluir._seq, serie=1,
            data_emissao=timezone.localtime(), status=status,
            peso_total_kg=Decimal('10'),
        )
        dados.update(kw)
        return MDFe.objects.create(**dados)

    def _documento(self, mdfe):
        from apps.logistica.models import DocumentoMDFe

        return DocumentoMDFe.objects.create(
            mdfe=mdfe, tipo_documento='nfe', numero_documento='1', serie='1',
            valor=Decimal('100'),
        )


class CancelarRascunhoTests(BaseMDFeCancelarExcluir):

    def _url(self, mdfe):
        return reverse('logistica:mdfe-cancelar-rascunho', args=[mdfe.pk])

    def test_cancela_um_rascunho(self):
        from apps.logistica.models import MDFe

        mdfe = self._mdfe(status='rascunho')
        resp = self.client.post(self._url(mdfe), {'justificativa': 'Pedido duplicado'})

        self.assertRedirects(resp, reverse('logistica:mdfe-detail', args=[mdfe.pk]))
        mdfe.refresh_from_db()
        self.assertEqual(mdfe.status, MDFe.Status.CANCELADO)
        self.assertEqual(mdfe.justificativa_cancelamento, 'Pedido duplicado')

    def test_justificativa_e_opcional(self):
        from apps.logistica.models import MDFe

        mdfe = self._mdfe(status='aguardando_nfe')
        self.client.post(self._url(mdfe), {})

        mdfe.refresh_from_db()
        self.assertEqual(mdfe.status, MDFe.Status.CANCELADO)

    def test_rejeitado_tambem_pode_ser_cancelado(self):
        from apps.logistica.models import MDFe

        mdfe = self._mdfe(status='rejeitado')
        self.client.post(self._url(mdfe), {})

        mdfe.refresh_from_db()
        self.assertEqual(mdfe.status, MDFe.Status.CANCELADO)

    def test_nao_cancela_um_mdfe_ja_autorizado(self):
        from apps.logistica.models import MDFe

        mdfe = self._mdfe(status='autorizado', chave_acesso='1' * 44)
        self.client.post(self._url(mdfe), {'justificativa': 'Tentativa indevida'})

        mdfe.refresh_from_db()
        self.assertEqual(mdfe.status, MDFe.Status.AUTORIZADO)

    def test_nao_cancela_um_mdfe_processando(self):
        from apps.logistica.models import MDFe

        mdfe = self._mdfe(status='processando')
        self.client.post(self._url(mdfe), {})

        mdfe.refresh_from_db()
        self.assertEqual(mdfe.status, MDFe.Status.PROCESSANDO)


class ExcluirMDFeTests(BaseMDFeCancelarExcluir):

    def _url(self, mdfe):
        return reverse('logistica:mdfe-delete', args=[mdfe.pk])

    def test_exclui_um_rascunho(self):
        from apps.logistica.models import MDFe

        mdfe = self._mdfe(status='rascunho')
        resp = self.client.post(self._url(mdfe))

        self.assertRedirects(resp, reverse('logistica:mdfe-list'))
        self.assertFalse(MDFe.objects.filter(pk=mdfe.pk).exists())

    def test_exclui_os_vinculos_com_documento_junto(self):
        from apps.logistica.models import DocumentoMDFe

        mdfe = self._mdfe(status='rascunho')
        doc = self._documento(mdfe)

        self.client.post(self._url(mdfe))

        self.assertFalse(DocumentoMDFe.objects.filter(pk=doc.pk).exists())

    def test_nao_exclui_um_mdfe_ja_autorizado(self):
        from apps.logistica.models import MDFe

        mdfe = self._mdfe(status='autorizado', chave_acesso='2' * 44)
        self.client.post(self._url(mdfe))

        self.assertTrue(MDFe.objects.filter(pk=mdfe.pk).exists())

    def test_nao_exclui_um_mdfe_encerrado(self):
        from apps.logistica.models import MDFe

        mdfe = self._mdfe(status='encerrado', chave_acesso='3' * 44)
        self.client.post(self._url(mdfe))

        self.assertTrue(MDFe.objects.filter(pk=mdfe.pk).exists())

    def test_mdfe_de_outra_filial_e_404(self):
        from apps.core.models import Filial

        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Outra', nome_fantasia='Outra',
            cnpj='21222333000262', uf='RN',
        )
        mdfe = self._mdfe(status='rascunho', filial=outra)

        resp = self.client.post(self._url(mdfe))

        self.assertEqual(resp.status_code, 404)
