from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from apps.core.models import Empresa, Filial
from apps.produtos.models import CategoriaProduto, CategoriaProdutoFilial
from apps.produtos.views.categoria import CategoriaListView


class CategoriaContextoEmpresaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa_usuario = Empresa.objects.create(
            razao_social='Empresa do usuario',
            cnpj='12345678000101',
            regime_tributario='simples_nacional',
            codigo_regime_tributario=1,
        )
        cls.empresa_ativa = Empresa.objects.create(
            razao_social='Empresa ativa',
            cnpj='12345678000102',
            regime_tributario='simples_nacional',
            codigo_regime_tributario=1,
        )
        cls.filial_ativa = Filial.objects.create(
            empresa=cls.empresa_ativa,
            razao_social='Filial ativa',
            cnpj='12345678000103',
            uf='RN',
        )
        cls.categoria = CategoriaProduto.objects.create(
            empresa=cls.empresa_ativa,
            filial=cls.filial_ativa,
            nome='CAMISAS',
        )
        cls.subcategoria = CategoriaProduto.objects.create(
            empresa=cls.empresa_ativa,
            filial=cls.filial_ativa,
            categoria_pai=cls.categoria,
            nome='CAMISAS BASICAS',
        )
        for categoria in (cls.categoria, cls.subcategoria):
            CategoriaProdutoFilial.objects.create(
                categoria=categoria,
                filial=cls.filial_ativa,
            )

    def test_lista_usa_empresa_da_filial_ativa_para_super_admin(self):
        request = RequestFactory().get('/produtos/categorias/')
        request.user = SimpleNamespace(empresa=self.empresa_usuario)
        request.filial_ativa = self.filial_ativa

        with patch('apps.produtos.views.categoria.render') as render_mock:
            CategoriaListView().get(request)

        contexto = render_mock.call_args.args[2]
        self.assertQuerySetEqual(
            contexto['categorias_tree'],
            [self.categoria],
        )
        self.assertQuerySetEqual(
            contexto['subcategorias'],
            [self.subcategoria],
        )
        self.assertEqual(contexto['total_categorias'], 1)
        self.assertEqual(contexto['total_subcategorias'], 1)
