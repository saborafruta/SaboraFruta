import importlib
from decimal import Decimal
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TransactionTestCase, override_settings

from apps.core.models import Empresa, Filial
from apps.produtos.models import (
    Produto,
    ProdutoApresentacao,
    ProdutoCodigoBarras,
    UnidadeMedida,
)
from apps.produtos.services.codigo_barras_service import ean13_valido


migration = importlib.import_module(
    'apps.produtos.migrations.0043_backfill_codigos_barras_obrigatorios'
)


class BackfillCodigosBarrasMigrationTests(TransactionTestCase):
    @override_settings(TENANT_DATABASE_ROUTING_ENABLED=True)
    def test_nao_altera_retrato_operacional_do_banco_gerencial(self):
        empresa = Empresa.objects.create(
            razao_social='Empresa Gerencial LTDA',
            nome_fantasia='Empresa Gerencial',
            cnpj='70345678000272',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        filial = Filial.objects.create(
            empresa=empresa,
            razao_social='Filial Gerencial',
            nome_fantasia='Filial Gerencial',
            cnpj='70345678000273',
            uf='RN',
        )
        unidade = UnidadeMedida.objects.create(
            empresa=empresa,
            sigla='UN',
            descricao='Unidade',
        )
        produto = Produto.objects.create(
            filial=filial,
            unidade_medida=unidade,
            descricao='Produto histórico',
            ncm='12345678',
        )

        migration.preencher_codigos_barras(
            apps,
            SimpleNamespace(connection=connection),
        )

        produto.refresh_from_db()
        self.assertEqual(produto.codigo_barras, '')

    def test_preenche_produto_e_apresentacao_sem_sobrescrever_e_e_idempotente(self):
        empresa = Empresa.objects.create(
            razao_social='Empresa Backfill LTDA',
            nome_fantasia='Empresa Backfill',
            cnpj='70345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        filial = Filial.objects.create(
            empresa=empresa,
            razao_social='Filial Backfill',
            nome_fantasia='Filial Backfill',
            cnpj='70345678000192',
            uf='RN',
        )
        unidade = UnidadeMedida.objects.create(
            empresa=empresa,
            sigla='UN',
            descricao='Unidade',
        )
        produto = Produto.objects.create(
            filial=filial,
            unidade_medida=unidade,
            descricao='Produto sem código',
            ncm='12345678',
        )
        preservado = Produto.objects.create(
            filial=filial,
            unidade_medida=unidade,
            descricao='Produto com código',
            codigo_barras='7891234567895',
            ncm='12345678',
        )
        fardo = ProdutoApresentacao.objects.create(
            produto=produto,
            unidade=unidade,
            descricao='Fardo com 10',
            fator_conversao=Decimal('10'),
        )
        schema_editor = SimpleNamespace(connection=connection)

        migration.preencher_codigos_barras(apps, schema_editor)
        migration.preencher_codigos_barras(apps, schema_editor)

        produto.refresh_from_db()
        preservado.refresh_from_db()
        self.assertTrue(ean13_valido(produto.codigo_barras))
        self.assertEqual(preservado.codigo_barras, '7891234567895')
        codigos = ProdutoCodigoBarras.objects.filter(apresentacao=fardo, ativo=True)
        self.assertEqual(codigos.count(), 1)
        self.assertTrue(ean13_valido(codigos.get().ean))
        self.assertEqual(codigos.get().quantidade_conversao, Decimal('10'))
