from django.test import TestCase

from apps.core.models import Empresa, Filial
from apps.financeiro.forms.pagar import ContaPagarForm
from apps.financeiro.forms.receber import ContaReceberForm
from apps.financeiro.models import PlanoContas


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
        cls.despesa = PlanoContas.objects.create(
            empresa=cls.empresa,
            codigo="331010001",
            descricao="Despesas com fornecedores",
            tipo="D",
            nivel=3,
            aceita_lancamento=True,
        )
        cls.receita = PlanoContas.objects.create(
            empresa=cls.empresa,
            codigo="311010001",
            descricao="Venda de mercadorias",
            tipo="R",
            nivel=3,
            aceita_lancamento=True,
        )
        cls.sintetica = PlanoContas.objects.create(
            empresa=cls.empresa,
            codigo="331",
            descricao="Despesas comerciais",
            tipo="D",
            nivel=1,
            aceita_lancamento=False,
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

    def test_conta_receber_lista_apenas_receitas_analiticas_ativas(self):
        form = ContaReceberForm(filial=self.filial)

        self.assertQuerySetEqual(
            form.fields["plano_contas"].queryset,
            [self.receita],
        )
