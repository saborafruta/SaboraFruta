from datetime import date
from decimal import Decimal
from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase

from apps.core.models import Empresa, Filial
from apps.financeiro.forms.pagar import ContaPagarForm
from apps.financeiro.forms.receber import ContaReceberForm
from apps.financeiro.models import ContaPagar, PlanoContabil, PlanoContas
from apps.financeiro.services.pagar_service import ContaPagarService


class PlanoContasLancamentosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Eureka",
            nome_fantasia="Eureka",
            cnpj="50649395000126",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social="Eureka",
            nome_fantasia="Eureka",
            cnpj="50649395000127",
            uf="RN",
        )
        cls.conta_contabil_despesa = PlanoContabil.objects.create(
            empresa=cls.empresa,
            codigo_referencia=101,
            classificacao="331010001",
            tipo_conta="A",
            descricao="Despesas com fornecedores",
            data_inicio=date(2015, 1, 1),
            nivel=5,
            ordem=1,
        )
        cls.conta_contabil_receita = PlanoContabil.objects.create(
            empresa=cls.empresa,
            codigo_referencia=102,
            classificacao="311010001",
            tipo_conta="A",
            descricao="Venda de mercadorias",
            data_inicio=date(2015, 1, 1),
            nivel=5,
            ordem=2,
        )
        cls.grupo_despesa = PlanoContas.objects.create(
            empresa=cls.empresa,
            codigo="331",
            descricao="Despesas comerciais",
            tipo="D",
            nivel=1,
            aceita_lancamento=False,
        )
        cls.subgrupo_despesa = PlanoContas.objects.create(
            empresa=cls.empresa,
            conta_pai=cls.grupo_despesa,
            codigo="33101",
            descricao="Despesas com vendas",
            tipo="D",
            nivel=2,
            aceita_lancamento=False,
        )
        cls.despesa = PlanoContas.objects.create(
            empresa=cls.empresa,
            conta_pai=cls.subgrupo_despesa,
            conta_contabil=cls.conta_contabil_despesa,
            codigo="331010001",
            descricao="Despesas com fornecedores",
            tipo="D",
            nivel=3,
            aceita_lancamento=True,
        )
        cls.receita = PlanoContas.objects.create(
            empresa=cls.empresa,
            conta_contabil=cls.conta_contabil_receita,
            codigo="311010001",
            descricao="Venda de mercadorias",
            tipo="R",
            nivel=3,
            aceita_lancamento=True,
        )
        PlanoContas.objects.create(
            empresa=cls.empresa,
            codigo="399999999",
            descricao="Despesa inativa",
            tipo="D",
            nivel=3,
            aceita_lancamento=True,
            ativo=False,
        )

    def test_conta_pagar_lista_apenas_despesas_analiticas_ativas(self):
        form = ContaPagarForm(filial=self.filial)

        self.assertQuerySetEqual(
            form.fields["plano_contas"].queryset,
            [self.despesa],
        )
        self.assertTrue(form.fields["plano_contas"].required)
        self.assertEqual(
            form.fields["plano_contas"].label_from_instance(self.despesa),
            "Despesas comerciais > Despesas com vendas > Despesas com fornecedores",
        )

    def test_conta_receber_lista_apenas_receitas_analiticas_ativas(self):
        form = ContaReceberForm(filial=self.filial)

        self.assertQuerySetEqual(
            form.fields["plano_contas"].queryset,
            [self.receita],
        )

    def test_lancamento_grava_conta_contabil_automaticamente(self):
        conta = ContaPagarService.criar(
            filial=self.filial,
            valor_original=Decimal("150.00"),
            data_emissao=date(2026, 8, 18),
            data_vencimento=date(2026, 8, 30),
            plano_contas=self.despesa,
        )

        self.assertEqual(conta.plano_contas, self.despesa)
        self.assertEqual(conta.conta_contabil, self.conta_contabil_despesa)

    def test_migration_vincula_categoria_e_titulo_existentes(self):
        conta_contabil = PlanoContabil.objects.create(
            empresa=self.empresa,
            codigo_referencia=103,
            classificacao="3320400001",
            tipo_conta="A",
            descricao="Energia elétrica",
            data_inicio=date(2015, 1, 1),
            nivel=5,
            ordem=3,
        )
        categoria = PlanoContas.objects.create(
            empresa=self.empresa,
            conta_pai=self.subgrupo_despesa,
            codigo="3320400001",
            descricao="Energia elétrica",
            tipo="D",
            nivel=3,
            aceita_lancamento=True,
        )
        titulo = ContaPagar.objects.create(
            filial=self.filial,
            plano_contas=categoria,
            valor_original=Decimal("300.00"),
            valor_final=Decimal("300.00"),
            valor_saldo=Decimal("300.00"),
            data_emissao=date(2026, 8, 18),
            data_vencimento=date(2026, 8, 30),
        )

        migration = import_module(
            "apps.financeiro.migrations.0017_categorias_financeiras_conta_contabil"
        )
        migration.vincular_categorias_e_titulos(django_apps, None)

        categoria.refresh_from_db()
        titulo.refresh_from_db()
        self.assertEqual(categoria.conta_contabil, conta_contabil)
        self.assertEqual(titulo.conta_contabil, conta_contabil)
