from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import ConfiguracaoAbcEstoque, Deposito, Estoque
from apps.estoque.services.equilibrio_estoque import calcular_equilibrio
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class EquilibrioComAjusteAbcTests(TestCase):
    """
    Fase 7: produto classe A (ou B/C) recebe multiplicador de minimo e
    dias extras de cobertura configurados por classe -- sem isso, o mesmo
    minimo vale igual pra qualquer produto, gire muito ou pouco.

    A demanda fica so' na Loja A (destino) e a Loja B so' guarda
    excedente (sem giro) -- e' o mesmo padrao usado em
    EquilibrioComLeadTimeTests pra poder calcular a meta a mao.
    """

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede ABC LTDA", nome_fantasia="Rede ABC",
            cnpj="88945678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.loja_a = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="88945678000192", uf="RN", is_matriz=True,
        )
        cls.loja_b = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja B", nome_fantasia="Loja B",
            cnpj="88945678000193", uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="abc@inoovated.com", nome="Usuario ABC", password="teste1234",
            empresa=cls.empresa, filial=cls.loja_a, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_a)
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_b)
        # Um so' produto vendido na rede -- fica 100% do volume, portanto
        # classe C na curva ABC por giro (padrao: <=80% A, <=95% B, resto C).
        cls.produto = Produto.objects.create(
            filial=cls.loja_a, unidade_medida=cls.unidade, descricao="Produto ABC",
            ncm="20089900", estoque_minimo=Decimal("5"), preco_venda=Decimal("10"),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_a)
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_b)
        deposito_a = Deposito.objects.create(filial=cls.loja_a, nome="Geral A", is_padrao=True)
        deposito_b = Deposito.objects.create(filial=cls.loja_b, nome="Geral B", is_padrao=True)
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_a, deposito=deposito_a,
            quantidade_atual=Decimal("5"), quantidade_disponivel=Decimal("5"),
        )
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_b, deposito=deposito_b,
            quantidade_atual=Decimal("25"), quantidade_disponivel=Decimal("25"),
        )
        venda = VendaPDV.objects.create(
            filial=cls.loja_a, numero_venda=1, usuario=cls.usuario,
            data_venda=timezone.now(), status="finalizada", valor_total=Decimal("300"),
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=cls.produto, numero_item=1, quantidade=Decimal("30"),
            unidade_medida="UN", valor_unitario=10, valor_total=Decimal("300"),
        )

    def test_sem_configuracao_abc_comportamento_e_o_padrao(self):
        resultado = calcular_equilibrio(empresa=self.empresa, dias_analise=30, dias_cobertura=14)
        sugestao = resultado["sugestoes"][0]
        self.assertEqual(sugestao["destino"], self.loja_a)
        # reserva_a = max(5, 0, 1*14) = 14; rede tem 11 de excedente alem
        # das reservas (30 - 19) e, como so' A tem demanda, ele herda tudo:
        # meta_a = 14 + 11 = 25.
        self.assertEqual(sugestao["destino_meta"], Decimal("25.000"))
        self.assertEqual(sugestao["quantidade"], Decimal("20"))

    def test_dias_extra_e_multiplicador_de_minimo_da_classe_mudam_a_meta(self):
        ConfiguracaoAbcEstoque.objects.create(
            empresa=self.empresa, classe=ConfiguracaoAbcEstoque.Classe.C,
            multiplicador_minimo=Decimal("2"), multiplicador_maximo=Decimal("1"),
            dias_cobertura_extra=6,
        )

        resultado = calcular_equilibrio(empresa=self.empresa, dias_analise=30, dias_cobertura=14)

        sugestao = resultado["sugestoes"][0]
        # dias_meta = 14+6 = 20; minimo ajustado = 5*2 = 10.
        # reserva_a = max(10, 0, 1*20=20) = 20; reserva_b = max(10,0,0)=10.
        # reserva_rede (30) agora bate exatamente o estoque_rede (30) --
        # sem excedente de rede pra redistribuir, a meta fica isolada
        # na propria reserva (20), sem o bonus de 11 que aparecia antes.
        self.assertEqual(sugestao["destino_meta"], Decimal("20.000"))
        self.assertEqual(sugestao["quantidade"], Decimal("15"))


class ConfiguracaoAbcViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede ABC View LTDA", nome_fantasia="Rede ABC View",
            cnpj="89045678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="89045678000192", uf="RN", is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="abcview@inoovated.com", nome="Usuario ABC View", password="teste1234",
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.usuario)

    def test_get_cria_as_tres_linhas_padrao(self):
        response = self.client.get(reverse("estoque:configuracao-abc"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ConfiguracaoAbcEstoque.objects.filter(empresa=self.empresa).count(), 3)

    def test_post_atualiza_as_tres_classes(self):
        self.client.get(reverse("estoque:configuracao-abc"))
        dados = {}
        for classe in ("A", "B", "C"):
            dados.update({
                f"{classe}-multiplicador_minimo": "1.5" if classe == "A" else "1",
                f"{classe}-multiplicador_maximo": "1",
                f"{classe}-dias_cobertura_extra": "5" if classe == "A" else "0",
            })
        response = self.client.post(reverse("estoque:configuracao-abc"), dados)
        self.assertRedirects(response, reverse("estoque:configuracao-abc"))
        linha_a = ConfiguracaoAbcEstoque.objects.get(empresa=self.empresa, classe="A")
        self.assertEqual(linha_a.multiplicador_minimo, Decimal("1.50"))
        self.assertEqual(linha_a.dias_cobertura_extra, 5)
