from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from apps.core.models import Empresa, Filial
from apps.produtos.models import (
    CategoriaProduto,
    CategoriaProdutoFilial,
    MarcaProduto,
    MarcaProdutoFilial,
    UnidadeMedida,
    UnidadeMedidaFilial,
)
from apps.produtos.views.categoria import CategoriaListView
from apps.produtos.views.marca import MarcaCreateView, MarcaListView
from apps.produtos.views.unidade import UnidadeCreateView, UnidadeListView


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
        cls.marca = MarcaProduto.objects.create(
            empresa=cls.empresa_ativa,
            filial=cls.filial_ativa,
            nome='ERK',
        )
        MarcaProdutoFilial.objects.create(
            marca=cls.marca,
            filial=cls.filial_ativa,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa_ativa,
            sigla='UN',
            descricao='Unidade',
        )
        UnidadeMedidaFilial.objects.create(
            unidade=cls.unidade,
            filial=cls.filial_ativa,
        )

    def request(self, path):
        request = RequestFactory().get(path)
        request.user = SimpleNamespace(empresa=self.empresa_usuario)
        request.filial_ativa = self.filial_ativa
        return request

    def test_lista_usa_empresa_da_filial_ativa_para_super_admin(self):
        request = self.request('/produtos/categorias/')

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

    def test_lista_marcas_usa_empresa_da_filial_ativa(self):
        with patch('apps.produtos.views.marca.render') as render_mock:
            MarcaListView().get(self.request('/produtos/marcas/'))

        contexto = render_mock.call_args.args[2]
        self.assertQuerySetEqual(contexto['marcas'], [self.marca])

    def test_lista_unidades_usa_empresa_da_filial_ativa(self):
        with patch('apps.produtos.views.unidade.render') as render_mock:
            UnidadeListView().get(self.request('/produtos/unidades/'))

        contexto = render_mock.call_args.args[2]
        self.assertQuerySetEqual(contexto['unidades'], [self.unidade])

    def test_criacao_marca_grava_na_empresa_da_filial_ativa(self):
        request = RequestFactory().post('/produtos/marcas/nova/', {'nome': 'NOVA MARCA'})
        request.user = SimpleNamespace(empresa=self.empresa_usuario)
        request.filial_ativa = self.filial_ativa

        with (
            patch('apps.produtos.views.marca._sincronizar_marca_sem_quebrar'),
            patch('apps.produtos.views.marca.messages.success'),
        ):
            response = MarcaCreateView().post(request)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            MarcaProduto.objects.filter(
                empresa=self.empresa_ativa,
                filial=self.filial_ativa,
                nome='NOVA MARCA',
            ).exists(),
        )

    def test_criacao_unidade_grava_na_empresa_da_filial_ativa(self):
        request = RequestFactory().post('/produtos/unidades/nova/', {
            'sigla': 'CX',
            'descricao': 'Caixa',
            'fator_conversao_base': '1',
            'casas_decimais': '0',
        })
        request.user = SimpleNamespace(empresa=self.empresa_usuario)
        request.filial_ativa = self.filial_ativa

        with (
            patch('apps.produtos.views.unidade.ReplicacaoProdutoService.sincronizar_unidade'),
            patch('apps.produtos.views.unidade.messages.success'),
        ):
            response = UnidadeCreateView().post(request)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            UnidadeMedida.objects.filter(
                empresa=self.empresa_ativa,
                sigla='CX',
            ).exists(),
        )
