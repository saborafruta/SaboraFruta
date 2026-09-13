from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TestCase

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque
from apps.estoque.services.equilibrio_estoque import calcular_equilibrio
from apps.pdv.models import VendaPDV
from apps.produtos.models import Produto


migracao = import_module("apps.pdv.migrations.0022_dados_teste_equilibrio_ited")


class DadosEquilibrioItedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="iTED Teste LTDA",
            nome_fantasia="iTED Teste",
            cnpj="06722483000113",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.matriz = Filial.objects.create(
            empresa=cls.empresa,
            razao_social="iTED Matriz",
            nome_fantasia="MATRIZ",
            cnpj=migracao.MATRIZ_CNPJ,
            uf="RN",
            is_matriz=True,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social="iTED Filial",
            nome_fantasia="FILIAL",
            cnpj=migracao.FILIAL_CNPJ,
            uf="RN",
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa,
            nome="Administrador",
            is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email="teste-equilibrio@ited.com",
            nome="Teste Equilibrio",
            password="teste1234",
            empresa=cls.empresa,
            filial=cls.matriz,
            perfil=perfil,
        )

    @staticmethod
    def _executar_carga():
        schema_editor = SimpleNamespace(connection=connection)
        migracao._semear_no_banco(apps, schema_editor)

    def test_carga_cria_apenas_dados_marcados_e_duas_sugestoes_opostas(self):
        self._executar_carga()

        vendas = VendaPDV.objects.filter(
            idempotency_key__startswith=migracao.MARCADOR,
        )
        self.assertEqual(vendas.count(), 12)
        self.assertEqual(vendas.filter(filial=self.matriz).count(), 6)
        self.assertEqual(vendas.filter(filial=self.filial).count(), 6)
        self.assertTrue(vendas.filter(data_venda__lt=vendas.latest("data_venda").created_at).exists())

        produto_matriz = Produto.objects.get(codigo="EQ-TESTE-MATRIZ")
        produto_filial = Produto.objects.get(codigo="EQ-TESTE-FILIAL")
        self.assertEqual(
            Estoque.objects.get(produto=produto_matriz, filial=self.matriz).quantidade_disponivel,
            Decimal("4.000"),
        )
        self.assertEqual(
            Estoque.objects.get(produto=produto_matriz, filial=self.filial).quantidade_disponivel,
            Decimal("500.000"),
        )
        self.assertEqual(
            Estoque.objects.get(produto=produto_filial, filial=self.matriz).quantidade_disponivel,
            Decimal("500.000"),
        )
        self.assertEqual(
            Estoque.objects.get(produto=produto_filial, filial=self.filial).quantidade_disponivel,
            Decimal("3.000"),
        )

        sugestoes = calcular_equilibrio(
            empresa=self.empresa,
            dias_analise=30,
            dias_cobertura=14,
        )["sugestoes"]
        direcoes = {
            (sugestao["produto"].codigo, sugestao["origem"].cnpj, sugestao["destino"].cnpj)
            for sugestao in sugestoes
        }
        self.assertIn(
            ("EQ-TESTE-MATRIZ", migracao.FILIAL_CNPJ, migracao.MATRIZ_CNPJ),
            direcoes,
        )
        self.assertIn(
            ("EQ-TESTE-FILIAL", migracao.MATRIZ_CNPJ, migracao.FILIAL_CNPJ),
            direcoes,
        )

    def test_carga_e_idempotente(self):
        self._executar_carga()
        totais_antes = list(
            Estoque.objects.filter(produto__codigo__startswith="EQ-TESTE-")
            .order_by("produto_id", "filial_id")
            .values_list("quantidade_atual", flat=True)
        )

        self._executar_carga()

        self.assertEqual(
            VendaPDV.objects.filter(idempotency_key__startswith=migracao.MARCADOR).count(),
            12,
        )
        self.assertEqual(
            list(
                Estoque.objects.filter(produto__codigo__startswith="EQ-TESTE-")
                .order_by("produto_id", "filial_id")
                .values_list("quantidade_atual", flat=True)
            ),
            totais_antes,
        )

    def test_nao_altera_empresa_que_nao_tem_os_dois_cnpjs_exatos(self):
        self.filial.cnpj = "06722483000999"
        self.filial.save(update_fields=["cnpj", "updated_at"])

        self._executar_carga()

        self.assertFalse(Produto.objects.filter(codigo__startswith="EQ-TESTE-").exists())
        self.assertFalse(
            VendaPDV.objects.filter(idempotency_key__startswith=migracao.MARCADOR).exists()
        )
