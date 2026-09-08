from pathlib import Path

from django.template.loader import get_template
from django.test import SimpleTestCase
from django.urls import reverse


class CentralAdminTemplateTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source = Path(
            'apps/core/templates/core/admin/central.html'
        ).read_text(encoding='utf-8')
        cls.base_source = Path('templates/_base.html').read_text(encoding='utf-8')

    def test_cabecalho_expoe_acessos_globais(self):
        for label in (
            'Bancos das empresas',
            'Gestão Railway',
            'Usuários e acessos',
            'Perfis e permissões',
        ):
            self.assertIn(label, self.source)
        self.assertEqual(
            reverse('core:admin_empresa_banco_list'),
            '/gestao/empresas/bancos/',
        )
        self.assertNotIn("admin:core_empresabanco_changelist", self.source)

    def test_template_compila(self):
        self.assertIsNotNone(get_template('core/admin/central.html'))

    def test_modulos_ficam_no_bloco_da_filial_selecionada(self):
        selection_start = self.source.index('{% if filial_selecionada %}')
        modules_button = self.source.index('>Módulos</button>')
        empty_selection = self.source.index(
            'Selecione uma empresa e uma filial de trabalho para filtrar',
            modules_button,
        )

        self.assertLess(selection_start, modules_button)
        self.assertLess(modules_button, empty_selection)
        self.assertNotIn('Vertical de cada empresa', self.source)

    def test_filiais_sao_filtradas_pela_empresa_escolhida(self):
        self.assertIn('data-empresa=', self.source)
        self.assertIn('filialPertence($el.dataset.empresa)', self.source)
        self.assertIn('selecionarEmpresa(', self.source)
        self.assertIn("this.filial = '';", self.base_source)
        self.assertIn('filialPertence: function(empresaDaFilial)', self.base_source)
        self.assertIn(':disabled="!empresa.trim()"', self.source)
