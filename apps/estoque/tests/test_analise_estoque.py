"""
Fase 3 do módulo de equalização de estoque: curva ABC por giro (volume
vendido, não receita) e produtos em excesso (saldo acima do máximo).
"""
from decimal import Decimal

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque
from apps.estoque.services.analise_estoque import classificar_abc_giro, produtos_em_excesso
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class AnaliseEstoqueBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Analise LTDA", nome_fantasia="Rede Analise",
            cnpj="85345678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.loja_a = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="85345678000192", uf="RN", is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="analise@inoovated.com", nome="Usuario Analise", password="teste1234",
            empresa=cls.empresa, filial=cls.loja_a, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_a)
        cls.deposito = Deposito.objects.create(filial=cls.loja_a, nome="Geral", is_padrao=True)

    def _produto(self, nome, estoque_maximo=Decimal("0")):
        produto = Produto.objects.create(
            filial=self.loja_a, unidade_medida=self.unidade, descricao=nome,
            ncm="20089900", preco_venda=Decimal("10"),
            estoque_maximo=estoque_maximo,
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.loja_a)
        return produto

    def _vender(self, produto, quantidade, numero):
        venda = VendaPDV.objects.create(
            filial=self.loja_a, numero_venda=numero, usuario=self.usuario,
            data_venda=timezone.now(), status="finalizada", valor_total=quantidade * 10,
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=produto, numero_item=1, quantidade=quantidade,
            unidade_medida="UN", valor_unitario=10, valor_total=quantidade * 10,
        )

    def _saldo(self, produto, quantidade):
        Estoque.objects.create(
            produto=produto, filial=self.loja_a, deposito=self.deposito,
            quantidade_atual=quantidade, quantidade_disponivel=quantidade,
        )


class CurvaAbcGiroServiceTests(AnaliseEstoqueBase):

    def test_classifica_por_participacao_acumulada_no_volume(self):
        # 70% acumulado -> A; +20% (90% acumulado) -> B; +10% (100%) -> C.
        alto_giro = self._produto("Alto giro")
        giro_medio = self._produto("Giro medio")
        baixo_giro = self._produto("Baixo giro")
        self._vender(alto_giro, Decimal("70"), numero=1)
        self._vender(giro_medio, Decimal("20"), numero=2)
        self._vender(baixo_giro, Decimal("10"), numero=3)

        resultado = classificar_abc_giro(empresa=self.empresa, dias_analise=30)

        itens_por_produto = {item["produto"].pk: item for item in resultado["itens"]}
        self.assertEqual(itens_por_produto[alto_giro.pk]["classe"], "A")
        self.assertEqual(itens_por_produto[giro_medio.pk]["classe"], "B")
        self.assertEqual(itens_por_produto[baixo_giro.pk]["classe"], "C")
        self.assertEqual(itens_por_produto[alto_giro.pk]["pct_acumulado"], Decimal("70.0"))

    def test_produto_sem_venda_no_periodo_nao_aparece(self):
        self._produto("Sem venda")

        resultado = classificar_abc_giro(empresa=self.empresa, dias_analise=30)

        self.assertEqual(resultado["itens"], [])

    def test_filtra_por_filial(self):
        outra_filial = Filial.objects.create(
            empresa=self.empresa, razao_social="Loja B", nome_fantasia="Loja B",
            cnpj="85345678000273", uf="RN",
        )
        produto = self._produto("Vendido na A")
        self._vender(produto, Decimal("5"), numero=1)

        resultado = classificar_abc_giro(empresa=self.empresa, filial=outra_filial, dias_analise=30)

        self.assertEqual(resultado["itens"], [])


class ProdutosEmExcessoServiceTests(AnaliseEstoqueBase):

    def test_saldo_acima_do_maximo_aparece(self):
        produto = self._produto("Encalhado", estoque_maximo=Decimal("10"))
        self._saldo(produto, Decimal("35"))

        itens = produtos_em_excesso(empresa=self.empresa)

        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0]["excedente"], Decimal("25.000"))

    def test_saldo_dentro_do_maximo_nao_aparece(self):
        produto = self._produto("Normal", estoque_maximo=Decimal("50"))
        self._saldo(produto, Decimal("35"))

        itens = produtos_em_excesso(empresa=self.empresa)

        self.assertEqual(itens, [])

    def test_produto_sem_maximo_cadastrado_nunca_aparece(self):
        produto = self._produto("Sem regra", estoque_maximo=Decimal("0"))
        self._saldo(produto, Decimal("999"))

        itens = produtos_em_excesso(empresa=self.empresa)

        self.assertEqual(itens, [])


class AnaliseEstoqueViewTests(AnaliseEstoqueBase):

    def test_curva_abc_abre(self):
        produto = self._produto("Visivel")
        self._vender(produto, Decimal("5"), numero=1)
        from apps.estoque.views.analise_estoque import CurvaAbcGiroView

        request = RequestFactory().get(reverse("estoque:curva-abc-giro"))
        request.user = self.usuario
        request.filial_ativa = self.loja_a
        request.session = {"filial_ativa_id": self.loja_a.pk}
        response = CurvaAbcGiroView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visivel")

    def test_produtos_excesso_abre(self):
        produto = self._produto("Parado", estoque_maximo=Decimal("5"))
        self._saldo(produto, Decimal("20"))
        from apps.estoque.views.analise_estoque import ProdutosExcessoView

        request = RequestFactory().get(reverse("estoque:produtos-excesso"))
        request.user = self.usuario
        request.filial_ativa = self.loja_a
        request.session = {"filial_ativa_id": self.loja_a.pk}
        response = ProdutosExcessoView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Parado")
