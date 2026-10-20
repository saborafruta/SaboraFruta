import importlib
from decimal import Decimal
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TransactionTestCase, override_settings

from apps.core.models import Empresa, Filial
from apps.produtos.models import Produto, UnidadeMedida, UnidadeMedidaFilial


migration = importlib.import_module(
    'apps.produtos.migrations.0044_unidades_comerciais_e_catalogo_ited'
)


class UnidadesCatalogoMigrationTests(TransactionTestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            razao_social='iTed',
            nome_fantasia='iTed',
            cnpj=migration.ITED_CNPJ,
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        self.matriz = Filial.objects.create(
            empresa=self.empresa,
            razao_social='Matriz',
            nome_fantasia='Matriz',
            cnpj='06722483000114',
            uf='RN',
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa,
            razao_social='Filial',
            nome_fantasia='Filial',
            cnpj='06722483000115',
            uf='RN',
        )

    def executar(self):
        migration.cadastrar_unidades_e_corrigir_catalogo(
            apps,
            SimpleNamespace(connection=connection),
        )

    def test_cria_catalogo_e_vincula_em_todas_as_filiais(self):
        UnidadeMedida.objects.create(
            empresa=self.empresa,
            sigla='G',
            descricao='GRAMA',
            tipo='peso',
            fator_conversao_base=1,
        )

        self.executar()
        self.executar()

        fardo = UnidadeMedida.objects.get(empresa=self.empresa, sigla='FD')
        grama = UnidadeMedida.objects.get(empresa=self.empresa, sigla='G')
        unidade = UnidadeMedida.objects.get(empresa=self.empresa, sigla='UN')
        self.assertEqual(fardo.descricao, 'Fardo')
        self.assertEqual(grama.fator_conversao_base, Decimal('0.001'))
        self.assertEqual(unidade.casas_decimais, 0)
        self.assertEqual(
            UnidadeMedidaFilial.objects.filter(unidade=fardo, ativo=True).count(),
            2,
        )
        self.assertEqual(
            UnidadeMedida.objects.filter(empresa=self.empresa).count(),
            len(migration.UNIDADES_COMERCIAIS),
        )

    def test_corrige_unidade_e_metadados_sem_mudar_saldo(self):
        grama = UnidadeMedida.objects.create(
            empresa=self.empresa,
            sigla='G',
            descricao='GRAMA',
            tipo='peso',
        )
        produto = Produto.objects.create(
            filial=self.matriz,
            unidade_medida=grama,
            unidade_medida_compra=grama,
            descricao='Saco PEAD 15x30 (1 kg) - 1.000 un',
            ncm='39232190',
            tipo_produto=Produto.TipoProduto.FRACIONADO,
            vendido_por_peso_granel=True,
            fracionavel=True,
        )

        self.executar()

        produto.refresh_from_db()
        self.assertEqual(produto.unidade_medida.sigla, 'PCT')
        self.assertEqual(produto.unidade_medida_compra.sigla, 'PCT')
        self.assertEqual(produto.tipo_produto, Produto.TipoProduto.UNITARIO)
        self.assertFalse(produto.vendido_por_peso_granel)
        self.assertFalse(produto.fracionavel)
        self.assertEqual(produto.tipo_embalagem, 'Pacote')
        self.assertEqual(produto.quantidade_por_embalagem, Decimal('1000'))

    @override_settings(TENANT_DATABASE_ROUTING_ENABLED=True)
    def test_nao_altera_retrato_do_banco_gerencial(self):
        self.executar()
        self.assertFalse(UnidadeMedida.objects.filter(empresa=self.empresa).exists())
