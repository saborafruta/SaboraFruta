from decimal import Decimal

from django.test import TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import FaixaCoberturaEstoque
from apps.estoque.services.cobertura_service import (
    FAIXA_PADRAO, carregar_faixas, classificar_cobertura, resolver_faixa,
)
from apps.produtos.models import CategoriaProduto, Produto, UnidadeMedida, UnidadeMedidaFilial


class ClassificarCoberturaTests(TestCase):
    def test_faixas_da_classificacao_padrao(self):
        self.assertEqual(classificar_cobertura(None, FAIXA_PADRAO), "")
        self.assertEqual(classificar_cobertura(Decimal("0"), FAIXA_PADRAO), "ruptura")
        self.assertEqual(classificar_cobertura(Decimal("3"), FAIXA_PADRAO), "critico")
        self.assertEqual(classificar_cobertura(Decimal("7"), FAIXA_PADRAO), "baixo")
        self.assertEqual(classificar_cobertura(Decimal("15"), FAIXA_PADRAO), "normal")
        self.assertEqual(classificar_cobertura(Decimal("30"), FAIXA_PADRAO), "alto")
        self.assertEqual(classificar_cobertura(Decimal("31"), FAIXA_PADRAO), "excesso")


class ResolverFaixaCascataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Cobertura LTDA", nome_fantasia="Rede Cobertura",
            cnpj="86745678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="86745678000192", uf="RN", is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="cobertura@inoovated.com", nome="Usuario Cobertura", password="teste1234",
            empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.categoria = CategoriaProduto.objects.create(empresa=cls.empresa, nome="Perecíveis")
        cls.produto_com_categoria = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade, descricao="Com categoria",
            ncm="20089900", preco_venda=Decimal("10"), categoria=cls.categoria,
        )
        cls.produto_sem_nada = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade, descricao="Sem override",
            ncm="20089900", preco_venda=Decimal("10"),
        )

    def test_sem_nenhuma_faixa_cadastrada_usa_o_padrao_embutido(self):
        faixas = carregar_faixas(empresa=self.empresa)
        resolvida = resolver_faixa(faixas, self.produto_sem_nada)
        self.assertEqual(resolvida, FAIXA_PADRAO)

    def test_produto_especifico_vence_categoria_e_padrao_da_empresa(self):
        FaixaCoberturaEstoque.objects.create(empresa=self.empresa, dias_critico=1, dias_baixo=2, dias_normal=3, dias_alto=4)
        FaixaCoberturaEstoque.objects.create(
            empresa=self.empresa, categoria=self.categoria,
            dias_critico=5, dias_baixo=6, dias_normal=7, dias_alto=8,
        )
        FaixaCoberturaEstoque.objects.create(
            empresa=self.empresa, produto=self.produto_com_categoria,
            dias_critico=9, dias_baixo=10, dias_normal=11, dias_alto=12,
        )

        faixas = carregar_faixas(empresa=self.empresa)

        resolvida_produto = resolver_faixa(faixas, self.produto_com_categoria)
        self.assertEqual(resolvida_produto.dias_critico, 9)

        # Produto sem override proprio mas com categoria que tem override:
        # deve pegar da categoria, nao do padrao da empresa.
        produto_so_categoria = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade, descricao="So categoria",
            ncm="20089900", preco_venda=Decimal("10"), categoria=self.categoria,
        )
        resolvida_categoria = resolver_faixa(faixas, produto_so_categoria)
        self.assertEqual(resolvida_categoria.dias_critico, 5)

        resolvida_padrao = resolver_faixa(faixas, self.produto_sem_nada)
        self.assertEqual(resolvida_padrao.dias_critico, 1)
