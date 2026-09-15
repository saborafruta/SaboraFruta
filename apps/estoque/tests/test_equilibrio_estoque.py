from decimal import Decimal

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque
from apps.estoque.services.equilibrio_estoque import calcular_equilibrio
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class EquilibrioEstoqueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Equilibrio LTDA", nome_fantasia="Rede Equilibrio",
            cnpj="82345678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.loja_a = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="82345678000192", uf="RN", is_matriz=True,
        )
        cls.loja_b = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja B", nome_fantasia="Loja B",
            cnpj="82345678000193", uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="equilibrio@inoovated.com", nome="Usuario Equilibrio", password="teste1234",
            empresa=cls.empresa, filial=cls.loja_a, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_a)
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_b)
        cls.produto = Produto.objects.create(
            filial=cls.loja_a, unidade_medida=cls.unidade, descricao="Produto de alto giro",
            ncm="20089900", estoque_minimo=Decimal("5"), preco_venda=Decimal("10"),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_a)
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_b)
        deposito_a = Deposito.objects.create(filial=cls.loja_a, nome="Geral A", is_padrao=True)
        deposito_b = Deposito.objects.create(filial=cls.loja_b, nome="Geral B", is_padrao=True)
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_a, deposito=deposito_a,
            quantidade_atual=2, quantidade_disponivel=2,
        )
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_b, deposito=deposito_b,
            quantidade_atual=40, quantidade_disponivel=40,
        )
        venda = VendaPDV.objects.create(
            filial=cls.loja_a, numero_venda=1, usuario=cls.usuario,
            data_venda=timezone.now(), status="finalizada", valor_total=300,
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=cls.produto, numero_item=1, quantidade=30,
            unidade_medida="UN", valor_unitario=10, valor_total=300,
        )

    def test_sugere_excedente_da_loja_parada_para_loja_de_alto_giro(self):
        resultado = calcular_equilibrio(
            empresa=self.empresa, dias_analise=30, dias_cobertura=14,
        )

        self.assertEqual(len(resultado["sugestoes"]), 1)
        sugestao = resultado["sugestoes"][0]
        self.assertEqual(sugestao["origem"], self.loja_b)
        self.assertEqual(sugestao["destino"], self.loja_a)
        self.assertEqual(sugestao["quantidade"], Decimal("35"))
        self.assertTrue(sugestao["produto_parado_origem"])
        self.assertEqual(Estoque.objects.get(filial=self.loja_b).quantidade_disponivel, Decimal("40"))

    def test_tela_exibe_sugestao_e_link_para_transferencia_quando_origem_ativa(self):
        from apps.estoque.views.equilibrio_estoque import EquilibrioEstoqueView

        request = RequestFactory().get(reverse("estoque:equilibrio-estoque"))
        request.user = self.usuario
        request.filial_ativa = self.loja_b
        request.session = {"filial_ativa_id": self.loja_b.pk}
        response = EquilibrioEstoqueView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Produto de alto giro")
        self.assertContains(response, "Criar transferência")
        self.assertContains(response, f"destino={self.loja_a.pk}")

    def test_produto_vinculado_a_uma_unica_filial_nao_e_comparado(self):
        ProdutoFilial.objects.filter(produto=self.produto, filial=self.loja_b).update(ativo=False)

        resultado = calcular_equilibrio(empresa=self.empresa)

        self.assertEqual(resultado["produtos_analisados"], 0)
        self.assertEqual(resultado["sugestoes"], [])

    def test_link_do_equilibrio_preenche_transferencia_com_validacao(self):
        from apps.estoque.views.outras_movimentacoes import TransferenciaLojaView

        request = RequestFactory().get(reverse("estoque:transferencia-lojas-create"), {
            "origem": "equilibrio",
            "destino": self.loja_a.pk,
            "produto": self.produto.pk,
            "quantidade": "12",
        })
        request.user = self.usuario
        request.filial_ativa = self.loja_b
        request.session = {"filial_ativa_id": self.loja_b.pk}
        response = TransferenciaLojaView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Produto de alto giro")
        self.assertContains(response, "Transferencia sugerida pelo equilibrio de estoque")

    def test_superusuario_central_usa_empresa_da_filial_ativa(self):
        from apps.estoque.views.equilibrio_estoque import EquilibrioEstoqueView

        outra_empresa = Empresa.objects.create(
            razao_social="Empresa alheia LTDA",
            nome_fantasia="Empresa alheia",
            cnpj="82345678000991",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        filial_alheia = Filial.objects.create(
            empresa=outra_empresa,
            razao_social="Filial alheia",
            nome_fantasia="L&R SPORTS",
            cnpj="82345678000992",
            uf="RN",
            is_matriz=True,
        )
        perfil_alheio = PerfilAcesso.objects.create(
            empresa=outra_empresa,
            nome="Super Admin alheio",
            is_admin=True,
        )
        superusuario_central = Usuario.objects.create_superuser(
            email="central@inoovated.com",
            nome="Super Admin",
            password="teste1234",
            empresa=outra_empresa,
            filial=filial_alheia,
            perfil=perfil_alheio,
        )

        request = RequestFactory().get(reverse("estoque:equilibrio-estoque"))
        request.user = superusuario_central
        request.filial_ativa = self.loja_a
        request.session = {"filial_ativa_id": self.loja_a.pk}
        response = EquilibrioEstoqueView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Loja A")
        self.assertContains(response, "Loja B")
        self.assertNotContains(response, "L&amp;R SPORTS")
