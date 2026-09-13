"""
Fases 10-12 do módulo de equalização: dashboard, mapa de estoque e central
de equalização -- todas consomem `posicoes_estoque.calcular_posicoes`,
uma leitura por produto+filial separada do motor de sugestão de
transferência (`equilibrio_estoque.calcular_equilibrio`), que já tem seus
próprios testes.
"""
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque
from apps.estoque.services.dashboard_equalizacao import montar_dashboard
from apps.estoque.services.mapa_estoque import detalhe_filial, montar_mapa
from apps.estoque.services.posicoes_estoque import calcular_posicoes
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class VisaoConsolidadaBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Visao LTDA", nome_fantasia="Rede Visao",
            cnpj="90145678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.loja_a = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="90145678000192", uf="RN", is_matriz=True,
        )
        cls.loja_b = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja B", nome_fantasia="Loja B",
            cnpj="90145678000273", uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="visao@inoovated.com", nome="Usuario Visao", password="teste1234",
            empresa=cls.empresa, filial=cls.loja_a, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_a)
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_b)
        cls.deposito_a = Deposito.objects.create(filial=cls.loja_a, nome="Geral A", is_padrao=True)
        cls.deposito_b = Deposito.objects.create(filial=cls.loja_b, nome="Geral B", is_padrao=True)

        # Produto parado (saldo alto, zero venda) na Loja A -- excedente.
        cls.produto_parado = Produto.objects.create(
            filial=cls.loja_a, unidade_medida=cls.unidade, descricao="Produto Parado",
            ncm="20089900", estoque_minimo=Decimal("0"), preco_custo=Decimal("5"),
        )
        ProdutoFilial.objects.create(produto=cls.produto_parado, filial=cls.loja_a)
        Estoque.objects.create(
            produto=cls.produto_parado, filial=cls.loja_a, deposito=cls.deposito_a,
            quantidade_atual=100, quantidade_disponivel=100,
        )

        # Produto em ruptura (saldo zero, com venda) na Loja B.
        cls.produto_ruptura = Produto.objects.create(
            filial=cls.loja_b, unidade_medida=cls.unidade, descricao="Produto Ruptura",
            ncm="20089900", estoque_minimo=Decimal("0"), preco_custo=Decimal("10"),
        )
        ProdutoFilial.objects.create(produto=cls.produto_ruptura, filial=cls.loja_b)
        Estoque.objects.create(
            produto=cls.produto_ruptura, filial=cls.loja_b, deposito=cls.deposito_b,
            quantidade_atual=0, quantidade_disponivel=0,
        )
        venda = VendaPDV.objects.create(
            filial=cls.loja_b, numero_venda=1, usuario=cls.usuario,
            data_venda=timezone.now(), status="finalizada", valor_total=Decimal("300"),
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=cls.produto_ruptura, numero_item=1, quantidade=Decimal("30"),
            unidade_medida="UN", valor_unitario=10, valor_total=Decimal("300"),
        )


class CalcularPosicoesTests(VisaoConsolidadaBase):
    def test_calcula_uma_posicao_por_produto_e_filial_vinculada(self):
        posicoes = calcular_posicoes(empresa=self.empresa, dias_analise=30, dias_cobertura=14)

        chaves = {(p["produto"].pk, p["filial"].pk) for p in posicoes}
        self.assertIn((self.produto_parado.pk, self.loja_a.pk), chaves)
        self.assertIn((self.produto_ruptura.pk, self.loja_b.pk), chaves)

    def test_produto_parado_tem_excedente_e_flag_parado(self):
        posicoes = calcular_posicoes(empresa=self.empresa, dias_analise=30, dias_cobertura=14)
        posicao = next(p for p in posicoes if p["produto"].pk == self.produto_parado.pk)

        self.assertTrue(posicao["parado"])
        self.assertEqual(posicao["excedente"], Decimal("100"))
        self.assertEqual(posicao["valor"], Decimal("500.00"))

    def test_produto_em_ruptura_tem_classe_ruptura_e_deficit(self):
        posicoes = calcular_posicoes(empresa=self.empresa, dias_analise=30, dias_cobertura=14)
        posicao = next(p for p in posicoes if p["produto"].pk == self.produto_ruptura.pk)

        self.assertEqual(posicao["classe"], "ruptura")
        self.assertGreater(posicao["deficit"], Decimal("0"))

    def test_filtra_por_filial(self):
        posicoes = calcular_posicoes(
            empresa=self.empresa, dias_analise=30, dias_cobertura=14, filial_id=self.loja_a.pk,
        )
        self.assertTrue(all(p["filial"].pk == self.loja_a.pk for p in posicoes))


class DashboardEqualizacaoTests(VisaoConsolidadaBase):
    def test_cards_refletem_ruptura_e_capital_parado(self):
        cards = montar_dashboard(empresa=self.empresa, dias_analise=30, dias_cobertura=14)

        self.assertEqual(cards["produtos_ruptura"], 1)
        self.assertEqual(cards["produtos_parados"], 1)
        self.assertEqual(cards["capital_parado"], Decimal("500.00"))
        self.assertEqual(cards["transferencias_aprovadas"], 0)
        self.assertEqual(cards["transferencias_em_transito"], 0)

    def test_view_abre(self):
        client = Client()
        client.force_login(self.usuario)
        response = client.get(reverse("estoque:dashboard-equalizacao"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Estoque total")


class MapaEstoqueTests(VisaoConsolidadaBase):
    def test_filial_com_ruptura_fica_vermelha(self):
        mapa = montar_mapa(empresa=self.empresa, dias_analise=30, dias_cobertura=14)
        info_b = next(item for item in mapa if item["filial"].pk == self.loja_b.pk)

        self.assertEqual(info_b["cor"], "vermelho")
        self.assertEqual(info_b["falta"], 1)

    def test_detalhe_filial_lista_produtos_em_falta(self):
        detalhe = detalhe_filial(empresa=self.empresa, filial_id=self.loja_b.pk, dias_analise=30, dias_cobertura=14)

        produtos_em_falta = {p["produto"].pk for p in detalhe["em_falta"]}
        self.assertIn(self.produto_ruptura.pk, produtos_em_falta)

    def test_view_abre_com_e_sem_drill_down(self):
        client = Client()
        client.force_login(self.usuario)
        response = client.get(reverse("estoque:mapa-estoque"))
        self.assertEqual(response.status_code, 200)

        response = client.get(reverse("estoque:mapa-estoque"), {"filial": self.loja_b.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Produto Ruptura")


class CentralEqualizacaoViewTests(VisaoConsolidadaBase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.usuario)

    def test_abre_sem_filtros(self):
        response = self.client.get(reverse("estoque:central-equalizacao"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Produto Parado")
        self.assertContains(response, "Produto Ruptura")

    def test_filtro_por_status_ruptura(self):
        response = self.client.get(reverse("estoque:central-equalizacao"), {"status": "ruptura"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Produto Ruptura")
        self.assertNotContains(response, "Produto Parado")

    def test_filtro_por_valor_minimo(self):
        response = self.client.get(reverse("estoque:central-equalizacao"), {"valor_minimo": "400"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Produto Parado")
        self.assertNotContains(response, "Produto Ruptura")
