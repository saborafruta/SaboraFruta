import importlib
from types import SimpleNamespace
from unittest import TestCase


migration = importlib.import_module(
    'apps.produtos.migrations.0035_apresentacao_principal_venda_compra'
)


class _Manager:
    def __init__(self):
        self.alias = None
        self.updated = None

    def using(self, alias):
        self.alias = alias
        return self

    def filter(self, *args, **kwargs):
        if self.alias is None:
            raise AssertionError('A consulta da migration precisa selecionar o banco alvo.')
        return self

    def update(self, **kwargs):
        self.updated = kwargs


class _Apps:
    def __init__(self, manager):
        self.model = SimpleNamespace(objects=manager)

    def get_model(self, app_label, model_name):
        return self.model


class Migration0035DatabaseTests(TestCase):
    def setUp(self):
        self.manager = _Manager()
        self.apps = _Apps(self.manager)
        self.schema_editor = SimpleNamespace(
            connection=SimpleNamespace(alias='empresa_teste')
        )

    def test_forward_usa_o_banco_em_migracao(self):
        migration.copiar_padrao_para_novas_flags(self.apps, self.schema_editor)

        self.assertEqual(self.manager.alias, 'empresa_teste')
        self.assertEqual(
            self.manager.updated,
            {'principal_venda': True, 'principal_compra': True},
        )

    def test_reverse_usa_o_banco_em_migracao(self):
        migration.copiar_novas_flags_para_padrao(self.apps, self.schema_editor)

        self.assertEqual(self.manager.alias, 'empresa_teste')
        self.assertEqual(self.manager.updated, {'padrao': True})
