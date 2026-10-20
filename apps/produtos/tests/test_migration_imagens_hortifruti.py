import importlib
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TransactionTestCase, override_settings

from apps.core.models import Empresa, Filial
from apps.produtos.models import Produto, UnidadeMedida


migration = importlib.import_module(
    'apps.produtos.migrations.0045_imagens_hortifruti_ited'
)


class ImagensHortifrutiMigrationTests(TransactionTestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            razao_social='iTed',
            nome_fantasia='iTed',
            cnpj=migration.ITED_CNPJ,
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        self.filial = Filial.objects.create(
            empresa=self.empresa,
            razao_social='Matriz',
            nome_fantasia='Matriz',
            cnpj='06722483000114',
            uf='RN',
        )
        self.unidade = UnidadeMedida.objects.create(
            empresa=self.empresa,
            sigla='UN',
            descricao='Unidade',
        )

    def executar(self):
        migration.cadastrar_imagens_hortifruti(
            apps,
            SimpleNamespace(connection=connection),
        )

    def produto(self, descricao, foto_url=''):
        return Produto.objects.create(
            filial=self.filial,
            unidade_medida=self.unidade,
            descricao=descricao,
            ncm='07099990',
            foto_url=foto_url,
        )

    def test_cadastra_imagem_cc0_por_descricao(self):
        produto = self.produto('Abacaxi Pérola')

        self.executar()
        self.executar()

        produto.refresh_from_db()
        self.assertEqual(
            produto.foto_url,
            migration.IMAGENS_HORTIFRUTI['Abacaxi Pérola'],
        )

    def test_preserva_imagem_ja_cadastrada(self):
        produto = self.produto('Abacaxi Pérola', 'https://example.com/minha-foto.jpg')

        self.executar()

        produto.refresh_from_db()
        self.assertEqual(produto.foto_url, 'https://example.com/minha-foto.jpg')

    def test_nao_preenche_produto_ambiguo(self):
        produto = self.produto('Banana Prata')

        self.executar()

        produto.refresh_from_db()
        self.assertEqual(produto.foto_url, '')

    @override_settings(TENANT_DATABASE_ROUTING_ENABLED=True)
    def test_nao_altera_retrato_do_banco_gerencial(self):
        produto = self.produto('Abacaxi Pérola')

        self.executar()

        produto.refresh_from_db()
        self.assertEqual(produto.foto_url, '')
