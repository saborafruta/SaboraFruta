"""
API de viagens com remessa autorizada -- alimenta o seletor da tela
"Venda Fora do Estabelecimento" no PDV.

SÓ AS QUE TÊM REMESSA AUTORIZADA E AINDA ESTÃO NA RUA: sem remessa, a venda
5103/6103 não teria o que a amparar; e uma viagem já finalizada/cancelada
não está mais vendendo nada, mesmo que a remessa dela ainda exista.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.financeiro.constants.enums import StatusDocumentoFiscal
from apps.financeiro.models.fiscal import DocumentoFiscal
from apps.logistica.models_viagem import Viagem


class ViagensRemessaAbertaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Viagens Remessa LTDA', nome_fantasia='Viagens Remessa',
            cnpj='82345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='82345678000192',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Operador PDV', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='viagens-remessa@inoovated.com', nome='Usuario',
            password='teste1234', empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()
        self.url = reverse('pdv:api_viagens_remessa_aberta')

    def _viagem(self, numero, status=Viagem.Status.EM_VENDAS, filial=None):
        return Viagem.objects.create(
            filial=filial or self.filial, numero=numero, status=status,
            motorista_nome='Seu Zé', veiculo_placa='ABC1D23', vendedor=self.usuario,
        )

    def _remessa(self, viagem, numero, status=StatusDocumentoFiscal.AUTORIZADA):
        return DocumentoFiscal.objects.create(
            filial=viagem.filial, tipo_documento='nfe',
            origem_tipo='viagem_remessa', origem_id=viagem.pk,
            numero=numero, serie=1, emitente_cnpj=viagem.filial.cnpj,
            destinatario_snapshot={}, valor_total=Decimal('100'),
            status=status, data_emissao=timezone.now(), usuario=self.usuario,
        )

    def test_lista_viagem_com_remessa_autorizada(self):
        viagem = self._viagem(numero=1)
        self._remessa(viagem, numero=500)

        resposta = self.client.get(self.url)

        dados = resposta.json()
        self.assertTrue(dados['ok'])
        self.assertEqual(len(dados['viagens']), 1)
        self.assertEqual(dados['viagens'][0]['id'], viagem.pk)
        self.assertEqual(dados['viagens'][0]['remessa_numero'], 500)

    def test_esconde_viagem_sem_remessa(self):
        self._viagem(numero=2)

        resposta = self.client.get(self.url)

        self.assertEqual(resposta.json()['viagens'], [])

    def test_esconde_viagem_com_remessa_ainda_nao_autorizada(self):
        viagem = self._viagem(numero=3)
        self._remessa(viagem, numero=501, status=StatusDocumentoFiscal.PROCESSANDO)

        resposta = self.client.get(self.url)

        self.assertEqual(resposta.json()['viagens'], [])

    def test_esconde_viagem_finalizada(self):
        viagem = self._viagem(numero=4, status=Viagem.Status.FINALIZADA)
        self._remessa(viagem, numero=502)

        resposta = self.client.get(self.url)

        self.assertEqual(resposta.json()['viagens'], [])

    def test_esconde_viagem_cancelada(self):
        viagem = self._viagem(numero=5, status=Viagem.Status.CANCELADA)
        self._remessa(viagem, numero=503)

        resposta = self.client.get(self.url)

        self.assertEqual(resposta.json()['viagens'], [])

    def test_esconde_viagem_de_outra_filial(self):
        outra_filial = Filial.objects.create(
            empresa=self.empresa, razao_social='Outra', cnpj='82345678000273',
            uf='RN', cidade='Mossoró',
        )
        viagem = self._viagem(numero=6, filial=outra_filial)
        self._remessa(viagem, numero=504)

        resposta = self.client.get(self.url)

        self.assertEqual(resposta.json()['viagens'], [])
