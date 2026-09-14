from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import NivelAprovacaoTransferencia


class NivelAprovacaoCrudTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Nivel LTDA", nome_fantasia="Rede Nivel",
            cnpj="96745678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="96745678000192", uf="RN", is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="nivel@inoovated.com", nome="Usuario Nivel", password="teste1234",
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.usuario)

    def test_cria_edita_e_exclui(self):
        response = self.client.post(reverse("estoque:nivel-aprovacao-create"), {
            "nivel_nome": "Supervisor", "valor_minimo": "0", "valor_maximo": "1000",
        })
        self.assertRedirects(response, reverse("estoque:nivel-aprovacao-list"))
        nivel = NivelAprovacaoTransferencia.objects.get(empresa=self.empresa)

        response = self.client.post(reverse("estoque:nivel-aprovacao-update", args=[nivel.pk]), {
            "nivel_nome": "Supervisor Senior", "valor_minimo": "0", "valor_maximo": "1500",
        })
        self.assertRedirects(response, reverse("estoque:nivel-aprovacao-list"))
        nivel.refresh_from_db()
        self.assertEqual(nivel.nivel_nome, "Supervisor Senior")

        response = self.client.post(reverse("estoque:nivel-aprovacao-delete", args=[nivel.pk]))
        self.assertRedirects(response, reverse("estoque:nivel-aprovacao-list"))
        self.assertFalse(NivelAprovacaoTransferencia.objects.filter(pk=nivel.pk).exists())

    def test_maximo_menor_que_minimo_e_invalido(self):
        response = self.client.post(reverse("estoque:nivel-aprovacao-create"), {
            "nivel_nome": "Invalido", "valor_minimo": "1000", "valor_maximo": "500",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(NivelAprovacaoTransferencia.objects.count(), 0)
