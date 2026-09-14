"""ItemTabelaPreco.apresentacao (Fase 16) -- preco de apresentacao por tabela/filial."""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.core.models import Empresa, Filial
from apps.produtos.models import (
    ItemTabelaPreco, Produto, ProdutoApresentacao, TabelaPreco, UnidadeMedida,
)


class ItemTabelaPrecoApresentacaoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Tabela Preco LTDA', cnpj='71345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL, codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Tabela Preco', cnpj='71345678000192', uf='RN',
        )
        cls.un = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='UN', descricao='Unidade')
        cls.cx = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='CX', descricao='Caixa')
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.un, descricao='Produto Teste', codigo='1001', ncm='39235000',
        )
        cls.apresentacao = ProdutoApresentacao.objects.create(
            produto=cls.produto, unidade=cls.cx, descricao='Caixa 10', fator_conversao=10,
        )
        cls.tabela = TabelaPreco.objects.create(filial=cls.filial, descricao='Varejo')

    def test_preco_default_sem_apresentacao(self):
        item = ItemTabelaPreco.objects.create(
            tabela=self.tabela, produto=self.produto, preco_unitario=Decimal('10.00'),
        )
        self.assertIsNone(item.apresentacao)

    def test_preco_especifico_por_apresentacao(self):
        item = ItemTabelaPreco.objects.create(
            tabela=self.tabela, produto=self.produto, apresentacao=self.apresentacao,
            preco_unitario=Decimal('90.00'),
        )
        self.assertEqual(item.apresentacao, self.apresentacao)

    def test_nao_permite_dois_precos_default_na_mesma_faixa(self):
        ItemTabelaPreco.objects.create(
            tabela=self.tabela, produto=self.produto, preco_unitario=Decimal('10.00'), quantidade_minima=0,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ItemTabelaPreco.objects.create(
                    tabela=self.tabela, produto=self.produto, preco_unitario=Decimal('11.00'), quantidade_minima=0,
                )

    def test_nao_permite_dois_precos_para_mesma_apresentacao_na_mesma_faixa(self):
        ItemTabelaPreco.objects.create(
            tabela=self.tabela, produto=self.produto, apresentacao=self.apresentacao,
            preco_unitario=Decimal('90.00'), quantidade_minima=0,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ItemTabelaPreco.objects.create(
                    tabela=self.tabela, produto=self.produto, apresentacao=self.apresentacao,
                    preco_unitario=Decimal('85.00'), quantidade_minima=0,
                )

    def test_preco_default_e_preco_da_apresentacao_coexistem(self):
        # Mesmo produto, mesma tabela, mesma faixa -- um default (produto na
        # unidade base) e um especifico da caixa. Nao deve colidir.
        ItemTabelaPreco.objects.create(
            tabela=self.tabela, produto=self.produto, preco_unitario=Decimal('10.00'), quantidade_minima=0,
        )
        item_caixa = ItemTabelaPreco.objects.create(
            tabela=self.tabela, produto=self.produto, apresentacao=self.apresentacao,
            preco_unitario=Decimal('90.00'), quantidade_minima=0,
        )
        self.assertEqual(ItemTabelaPreco.objects.filter(tabela=self.tabela, produto=self.produto).count(), 2)
        self.assertEqual(item_caixa.preco_unitario, Decimal('90.00'))
