"""Fases 27-28: relatórios e indicadores de performance."""
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque, MovimentacaoEstoque
from apps.estoque.services.indicadores_performance import calcular_indicadores
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.estoque.services.relatorios_equalizacao import (
    relatorio_produtos_mais_transferidos, relatorio_produtos_parados,
    relatorio_ruptura, relatorio_transferencias_por_filial,
)
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class RelatoriosIndicadoresBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Relatorios LTDA", nome_fantasia="Rede Relatorios",
            cnpj="99045678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.loja_a = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="99045678000192", uf="RN", is_matriz=True,
        )
        cls.loja_b = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja B", nome_fantasia="Loja B",
            cnpj="99045678000273", uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="relatorios@inoovated.com", nome="Usuario Relatorios", password="teste1234",
            empresa=cls.empresa, filial=cls.loja_a, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_a)
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_b)
        cls.produto_ruptura = Produto.objects.create(
            filial=cls.loja_a, unidade_medida=cls.unidade, descricao="Produto Ruptura",
            ncm="20089900", preco_venda=Decimal("10"), preco_custo=Decimal("4"),
        )
        ProdutoFilial.objects.create(produto=cls.produto_ruptura, filial=cls.loja_a)
        cls.produto_parado = Produto.objects.create(
            filial=cls.loja_a, unidade_medida=cls.unidade, descricao="Produto Parado",
            ncm="20089900", preco_venda=Decimal("10"), preco_custo=Decimal("4"),
        )
        ProdutoFilial.objects.create(produto=cls.produto_parado, filial=cls.loja_a)
        deposito_a = Deposito.objects.create(filial=cls.loja_a, nome="Geral A", is_padrao=True)
        Estoque.objects.create(
            produto=cls.produto_ruptura, filial=cls.loja_a, deposito=deposito_a,
            quantidade_atual=0, quantidade_disponivel=0,
        )
        Estoque.objects.create(
            produto=cls.produto_parado, filial=cls.loja_a, deposito=deposito_a,
            quantidade_atual=100, quantidade_disponivel=100,
        )
        venda = VendaPDV.objects.create(
            filial=cls.loja_a, numero_venda=1, usuario=cls.usuario,
            data_venda=timezone.now(), status="finalizada", valor_total=Decimal("100"),
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=cls.produto_ruptura, numero_item=1, quantidade=Decimal("10"),
            unidade_medida="UN", valor_unitario=10, valor_total=Decimal("100"),
        )
        # Uma transferencia real de A pra B, pro relatorio por filial/mais transferidos.
        Estoque.objects.create(
            produto=cls.produto_parado, filial=cls.loja_b, deposito=Deposito.objects.create(filial=cls.loja_b, nome="Geral B", is_padrao=True),
            quantidade_atual=0, quantidade_disponivel=0,
        )
        ProdutoFilial.objects.create(produto=cls.produto_parado, filial=cls.loja_b)
        MovimentacaoService.transferir_entre_filiais(
            produto_id=cls.produto_parado.pk, filial_origem_id=cls.loja_a.pk, filial_destino_id=cls.loja_b.pk,
            quantidade=Decimal("20"), usuario_id=cls.usuario.pk, permitir_sem_lote=True,
            documento_numero="TRF-REL-1",
        )


class IndicadoresPerformanceTests(RelatoriosIndicadoresBase):
    def test_indicadores_atuais_refletem_o_estado(self):
        resultado = calcular_indicadores(empresa=self.empresa)

        self.assertGreater(resultado["atuais"]["taxa_ruptura_pct"], Decimal("0"))
        self.assertGreater(resultado["atuais"]["capital_parado"], Decimal("0"))

    def test_historico_conta_a_transferencia_real(self):
        resultado = calcular_indicadores(empresa=self.empresa)

        self.assertEqual(resultado["historico"]["transferencias_realizadas"], 0)  # etapa nasce Aprovada, nao Recebida
        self.assertGreater(resultado["historico"]["valor_redistribuido"], Decimal("0"))


class RelatoriosEqualizacaoServiceTests(RelatoriosIndicadoresBase):
    def test_relatorio_ruptura_lista_o_produto_zerado(self):
        itens = relatorio_ruptura(empresa=self.empresa)
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0]["produto"], self.produto_ruptura)

    def test_relatorio_produtos_parados_lista_o_produto_sem_giro(self):
        itens = relatorio_produtos_parados(empresa=self.empresa)
        produtos = {item["produto"] for item in itens}
        self.assertIn(self.produto_parado, produtos)
        self.assertNotIn(self.produto_ruptura, produtos)

    def test_relatorio_transferencias_por_filial(self):
        itens = relatorio_transferencias_por_filial(empresa=self.empresa, dias=30)
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0]["filial"], self.loja_a)
        self.assertEqual(itens[0]["quantidade"], Decimal("20"))

    def test_relatorio_produtos_mais_transferidos(self):
        itens = relatorio_produtos_mais_transferidos(empresa=self.empresa, dias=30)
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0]["produto"], self.produto_parado)
        self.assertEqual(itens[0]["quantidade"], Decimal("20"))


class RelatoriosEqualizacaoViewTests(RelatoriosIndicadoresBase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.usuario)

    def test_central_abre_com_todas_as_secoes(self):
        response = self.client.get(reverse("estoque:relatorios-equalizacao"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Produto Ruptura")
        self.assertContains(response, "Produto Parado")
        self.assertContains(response, "Taxa de ruptura")
