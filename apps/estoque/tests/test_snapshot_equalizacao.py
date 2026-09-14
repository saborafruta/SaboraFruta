"""Fase 28/29: snapshot histórico das sugestões do equilíbrio."""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Deposito, Estoque, SugestaoEqualizacaoSnapshot
from apps.estoque.services.snapshot_equalizacao import gerar_snapshot_equilibrio
from apps.estoque.tasks.equalizacao import _pipeline_equalizacao_banco_atual
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class SnapshotEqualizacaoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Rede Snapshot LTDA", nome_fantasia="Rede Snapshot",
            cnpj="97845678000191", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.loja_a = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja A", nome_fantasia="Loja A",
            cnpj="97845678000192", uf="RN", is_matriz=True,
        )
        cls.loja_b = Filial.objects.create(
            empresa=cls.empresa, razao_social="Loja B", nome_fantasia="Loja B",
            cnpj="97845678000273", uf="RN",
        )
        cls.perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome="Admin", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="snapshot@inoovated.com", nome="Usuario Snapshot", password="teste1234",
            empresa=cls.empresa, filial=cls.loja_a, perfil=cls.perfil,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla="UN", descricao="Unidade", tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_a)
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.loja_b)
        cls.produto = Produto.objects.create(
            filial=cls.loja_a, unidade_medida=cls.unidade, descricao="Produto Snapshot",
            ncm="20089900", preco_venda=Decimal("10"),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_a)
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.loja_b)
        deposito_a = Deposito.objects.create(filial=cls.loja_a, nome="Geral A", is_padrao=True)
        deposito_b = Deposito.objects.create(filial=cls.loja_b, nome="Geral B", is_padrao=True)
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_a, deposito=deposito_a,
            quantidade_atual=500, quantidade_disponivel=500,
        )
        Estoque.objects.create(
            produto=cls.produto, filial=cls.loja_b, deposito=deposito_b,
            quantidade_atual=0, quantidade_disponivel=0,
        )
        venda = VendaPDV.objects.create(
            filial=cls.loja_b, numero_venda=1, usuario=cls.usuario,
            data_venda=timezone.now(), status="finalizada", valor_total=Decimal("300"),
        )
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=cls.produto, numero_item=1, quantidade=Decimal("30"),
            unidade_medida="UN", valor_unitario=10, valor_total=Decimal("300"),
        )

    def test_gerar_snapshot_grava_uma_linha_por_sugestao(self):
        criadas = gerar_snapshot_equilibrio(self.empresa)

        self.assertEqual(criadas, 1)
        linha = SugestaoEqualizacaoSnapshot.objects.get(empresa=self.empresa)
        self.assertEqual(linha.produto, self.produto)
        self.assertEqual(linha.filial_origem, self.loja_a)
        self.assertEqual(linha.filial_destino, self.loja_b)
        self.assertGreater(linha.quantidade_sugerida, Decimal("0"))

    def test_gerar_snapshot_nao_mexe_no_estoque(self):
        gerar_snapshot_equilibrio(self.empresa)

        estoque_a = Estoque.objects.get(produto=self.produto, filial=self.loja_a)
        self.assertEqual(estoque_a.quantidade_atual, Decimal("500"))

    def test_pipeline_task_processa_a_empresa_e_retorna_contagem(self):
        resultado = _pipeline_equalizacao_banco_atual()

        self.assertEqual(resultado["empresas"], 1)
        self.assertEqual(resultado["sugestoes"], 1)
        self.assertEqual(SugestaoEqualizacaoSnapshot.objects.count(), 1)
