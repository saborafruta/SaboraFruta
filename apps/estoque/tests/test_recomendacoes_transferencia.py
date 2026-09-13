"""Fases 13-14: tabela de recomendações com explicação em texto por linha."""
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque
from apps.estoque.services.equilibrio_estoque import calcular_equilibrio
from apps.estoque.services.motivo_transferencia import enriquecer_para_tabela, gerar_motivo
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class RecomendacoesBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Recomendacoes LTDA", nome_fantasia="Rede Recomendacoes",
            cnpj="91245678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.loja_a = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="91245678000192", uf="RN", is_matriz=True,
        )
        cls.loja_b = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja B", nome_fantasia="Loja B",
            cnpj="91245678000273", uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="recomendacoes@inoovated.com", nome="Usuario Recomendacoes", password="teste1234",
            empresa=cls.empresa, filial=cls.loja_a, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_a)
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_b)
        cls.produto = Produto.objects.create(
            filial=cls.loja_a, unidade_medida=cls.unidade, descricao="Polpa de Morango 100g",
            codigo="PM100", ncm="20089900", estoque_minimo=Decimal("0"),
            preco_custo=Decimal("3.50"), preco_venda=Decimal("8"),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_a)
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_b)
        deposito_a = Deposito.objects.create(filial=cls.loja_a, nome="Geral A", is_padrao=True)
        deposito_b = Deposito.objects.create(filial=cls.loja_b, nome="Geral B", is_padrao=True)
        # Loja A parada (excedente), Loja B vendendo e quase em ruptura.
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_a, deposito=deposito_a,
            quantidade_atual=500, quantidade_disponivel=500,
        )
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_b, deposito=deposito_b,
            quantidade_atual=10, quantidade_disponivel=10,
        )
        venda = VendaPDV.objects.create(
            filial=cls.loja_b, numero_venda=1, usuario=cls.usuario,
            data_venda=timezone.now(), status="finalizada", valor_total=Decimal("2400"),
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=cls.produto, numero_item=1, quantidade=Decimal("240"),
            unidade_medida="UN", valor_unitario=8, valor_total=Decimal("2400"),
        )

    def _sugestao(self):
        resultado = calcular_equilibrio(empresa=self.empresa, dias_analise=30, dias_cobertura=14)
        return resultado["sugestoes"][0]


class EnriquecerParaTabelaTests(RecomendacoesBase):
    def test_campos_de_apresentacao_sao_calculados(self):
        sugestao = self._sugestao()

        r = enriquecer_para_tabela(sugestao, dias_analise=30)

        self.assertEqual(r["sku"], "PM100")
        self.assertEqual(r["custo_unitario"], Decimal("3.50"))
        self.assertEqual(r["valor_transferencia"], (r["quantidade"] * Decimal("3.50")).quantize(Decimal("0.01")))
        self.assertIn(r["prioridade_label"], ("Alta", "Média", "Baixa"))
        self.assertEqual(r["status_label"], "Sugerida")
        # Origem esta parada (sem venda no periodo) -- cobertura "sem giro"
        # (None) antes e depois, nao um numero de dias.
        self.assertIsNone(r["origem_cobertura"])
        self.assertIsNone(r["origem_cobertura_apos"])
        # Destino vende de verdade: apos receber a transferencia, a
        # cobertura sobe em relacao a antes.
        self.assertGreater(r["destino_cobertura_apos"], r["destino_cobertura"])

    def test_motivo_cita_filiais_cobertura_e_quantidade_reais(self):
        sugestao = self._sugestao()
        r = enriquecer_para_tabela(sugestao, dias_analise=30)

        motivo = gerar_motivo(r)

        self.assertIn("Loja A", motivo)
        self.assertIn("Loja B", motivo)
        self.assertIn(str(r["quantidade"].normalize()), motivo)
        self.assertIn(str(r["destino_cobertura"]), motivo)


class RecomendacoesTransferenciaViewTests(RecomendacoesBase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.usuario)

    def test_tabela_abre_com_produto_e_motivo(self):
        response = self.client.get(reverse("estoque:recomendacoes-transferencia"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Polpa de Morango 100g")
        self.assertContains(response, "Por que essa transferência?")
        self.assertContains(response, "Recomendamos transferir")

    def test_filtro_por_origem_e_destino(self):
        response = self.client.get(
            reverse("estoque:recomendacoes-transferencia"),
            {"origem": self.loja_a.pk, "destino": self.loja_b.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Polpa de Morango 100g")
