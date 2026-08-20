from datetime import date
from decimal import Decimal
from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase

from apps.cadastros.forms import FuncionarioForm
from apps.cadastros.models import Funcionario
from apps.core.models import Empresa, Filial
from apps.financeiro.forms.pagar import ContaPagarForm
from apps.financeiro.models import ContaPagar, PlanoContabil, PlanoContas
from apps.financeiro.services.pagar_service import ContaPagarService


class FuncionarioContaPagarTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Eureka", nome_fantasia="Eureka", cnpj="50649395000126",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social="Eureka", nome_fantasia="Eureka",
            cnpj="50649395000127", uf="RN",
        )
        cls.funcionario = Funcionario.objects.create(
            filial=cls.filial, nome="Maria Silva", cpf="12345678901",
            cargo="Costureira", salario_base=Decimal("1800.00"), chave_pix="12345678901",
        )
        cls.conta_contabil = PlanoContabil.objects.create(
            empresa=cls.empresa, codigo_referencia=8001, classificacao="3320100001",
            tipo_conta="A", descricao="SALARIOS E ORDENADOS", data_inicio=date(2015, 1, 1),
            nivel=5, ordem=1,
        )
        cls.categoria = PlanoContas.objects.create(
            empresa=cls.empresa, conta_contabil=cls.conta_contabil,
            codigo="3320100001", descricao="Salarios e Ordenados", tipo="D",
            nivel=3, aceita_lancamento=True,
        )

    def dados_formulario(self, **extras):
        dados = {
            "tipo_lancamento": "funcionario",
            "funcionario": self.funcionario.pk,
            "documento_numero": "FOLHA-08/2026",
            "parcela": 1,
            "total_parcelas": 1,
            "valor_original": "1800.00",
            "data_emissao": "2026-08-20",
            "data_vencimento": "2026-08-30",
            "plano_contas": self.categoria.pk,
        }
        dados.update(extras)
        return dados

    def test_funcionario_exige_pessoa_se_for_pagamento_direto(self):
        form = ContaPagarForm(self.dados_formulario(funcionario=""), filial=self.filial)
        self.assertFalse(form.is_valid())
        self.assertIn("funcionario", form.errors)

    def test_encargo_geral_nao_exige_funcionario(self):
        form = ContaPagarForm(
            self.dados_formulario(tipo_lancamento="encargo", funcionario=""),
            filial=self.filial,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_servico_grava_funcionario_categoria_e_conta_contabil(self):
        conta = ContaPagarService.criar(
            filial=self.filial, funcionario=self.funcionario, tipo_lancamento="funcionario",
            valor_original=Decimal("1800.00"), data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 30), plano_contas=self.categoria,
        )
        self.assertEqual(conta.funcionario, self.funcionario)
        self.assertIsNone(conta.fornecedor)
        self.assertEqual(conta.conta_contabil, self.conta_contabil)
        self.assertEqual(conta.beneficiario_nome, "Maria Silva")

    def test_cpf_duplicado_na_filial_e_bloqueado(self):
        form = FuncionarioForm({"nome": "Outra Maria", "cpf": "123.456.789-01", "salario_base": "0"}, filial=self.filial)
        self.assertFalse(form.is_valid())
        self.assertIn("cpf", form.errors)

    def test_migration_cria_tres_niveis_da_categoria_de_pessoal(self):
        migration = import_module("apps.financeiro.migrations.0018_funcionario_conta_pagar_categorias_pessoal")
        migration.criar_categorias_pessoal(django_apps, None)
        categoria = PlanoContas.objects.get(empresa=self.empresa, codigo="3320100001")
        self.assertEqual(categoria.conta_pai.codigo, "33201")
        self.assertEqual(categoria.conta_pai.conta_pai.codigo, "332")
        self.assertEqual(categoria.conta_contabil, self.conta_contabil)

    def test_recorrencia_mensal_cria_titulos_com_datas_validas(self):
        contas = ContaPagarService.criar_recorrencia(
            filial=self.filial,
            funcionario=self.funcionario,
            tipo_lancamento="funcionario",
            valor_original=Decimal("1800.00"),
            data_emissao=date(2026, 8, 20),
            data_vencimento=date(2026, 8, 31),
            competencia=date(2026, 8, 1),
            plano_contas=self.categoria,
            frequencia="mensal",
            quantidade=3,
        )

        self.assertEqual([conta.data_vencimento for conta in contas], [
            date(2026, 8, 31), date(2026, 9, 30), date(2026, 10, 31),
        ])
        self.assertEqual([conta.competencia for conta in contas], [
            date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1),
        ])
        self.assertEqual([conta.parcela for conta in contas], [1, 2, 3])
        self.assertTrue(all(conta.total_parcelas == 3 for conta in contas))
        self.assertEqual(len({conta.grupo_recorrencia for conta in contas}), 1)
        self.assertIsNotNone(contas[0].grupo_recorrencia)
