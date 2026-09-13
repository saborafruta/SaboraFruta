from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import FaixaCoberturaEstoque
from apps.produtos.models import CategoriaProduto, Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class FaixaCoberturaCrudViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Faixa CRUD LTDA", nome_fantasia="Rede Faixa CRUD",
            cnpj="87845678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="87845678000192", uf="RN", is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="faixacrud@inoovated.com", nome="Usuario Faixa CRUD", password="teste1234",
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.categoria = CategoriaProduto.objects.create(empresa=cls.empresa, nome="Perecíveis")
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade, descricao="Produto Faixa",
            ncm="20089900", preco_venda=Decimal("10"),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.usuario)

    def test_lista_abre_vazia(self):
        response = self.client.get(reverse("estoque:faixa-cobertura-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "padrão embutido")

    def test_cria_faixa_padrao_da_empresa(self):
        response = self.client.post(reverse("estoque:faixa-cobertura-create"), {
            "categoria": "", "produto": "",
            "dias_critico": 3, "dias_baixo": 7, "dias_normal": 15, "dias_alto": 30,
        })
        self.assertRedirects(response, reverse("estoque:faixa-cobertura-list"))
        self.assertEqual(FaixaCoberturaEstoque.objects.filter(empresa=self.empresa).count(), 1)

    def test_bloqueia_produto_e_categoria_juntos(self):
        response = self.client.post(reverse("estoque:faixa-cobertura-create"), {
            "categoria": self.categoria.pk, "produto": self.produto.pk,
            "dias_critico": 3, "dias_baixo": 7, "dias_normal": 15, "dias_alto": 30,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FaixaCoberturaEstoque.objects.filter(empresa=self.empresa).count(), 0)

    def test_edita_e_exclui(self):
        faixa = FaixaCoberturaEstoque.objects.create(
            empresa=self.empresa, produto=self.produto,
            dias_critico=2, dias_baixo=5, dias_normal=10, dias_alto=20,
        )
        response = self.client.post(reverse("estoque:faixa-cobertura-update", args=[faixa.pk]), {
            "categoria": "", "produto": self.produto.pk,
            "dias_critico": 4, "dias_baixo": 8, "dias_normal": 16, "dias_alto": 32,
        })
        self.assertRedirects(response, reverse("estoque:faixa-cobertura-list"))
        faixa.refresh_from_db()
        self.assertEqual(faixa.dias_critico, 4)

        response = self.client.post(reverse("estoque:faixa-cobertura-delete", args=[faixa.pk]))
        self.assertRedirects(response, reverse("estoque:faixa-cobertura-list"))
        self.assertFalse(FaixaCoberturaEstoque.objects.filter(pk=faixa.pk).exists())
