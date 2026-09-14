"""Fase 33: seed de demonstração do módulo de equalização."""
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.core.models import Empresa, Filial
from apps.estoque.models import Estoque
from apps.estoque.services.equilibrio_estoque import calcular_equilibrio
from apps.produtos.models import Produto


class SeedEqualizacaoDemoTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            razao_social="Rede Seed LTDA", nome_fantasia="Rede Seed",
            cnpj="97945678000970", regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )

    def test_seed_cria_cenario_com_sugestao_de_transferencia(self):
        saida = StringIO()
        call_command("seed_equalizacao_demo", empresa_cnpj=self.empresa.cnpj, stdout=saida)

        self.assertEqual(Filial.objects.filter(empresa=self.empresa).count(), 2)
        produto = Produto.objects.get(descricao__icontains="demo equalização")
        self.assertTrue(Estoque.objects.filter(produto=produto).exists())

        resultado = calcular_equilibrio(empresa=self.empresa, dias_analise=30, dias_cobertura=14)
        self.assertEqual(len(resultado["sugestoes"]), 1)
        self.assertIn("sugestão de transferência", saida.getvalue())

    def test_seed_e_idempotente(self):
        call_command("seed_equalizacao_demo", empresa_cnpj=self.empresa.cnpj, stdout=StringIO())
        call_command("seed_equalizacao_demo", empresa_cnpj=self.empresa.cnpj, stdout=StringIO())

        self.assertEqual(Filial.objects.filter(empresa=self.empresa).count(), 2)
        self.assertEqual(Produto.objects.filter(descricao__icontains="demo equalização").count(), 1)

    def test_seed_com_cnpj_inexistente_falha(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("seed_equalizacao_demo", empresa_cnpj="00000000000000", stdout=StringIO())
