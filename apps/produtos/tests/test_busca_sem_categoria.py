from types import SimpleNamespace

from django.test import RequestFactory, TestCase

from apps.core.models import Empresa, Filial
from apps.estoque.views.estoque import AjusteRapidoEstoqueView
from apps.produtos.models import (
    CategoriaProduto, CategoriaProdutoFilial, Produto, ProdutoFilial, UnidadeMedida,
)
from apps.produtos.views.produto import _produto_queryset_filtrado


class BuscaProdutoSemCategoriaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Busca', cnpj='12345678000191',
            regime_tributario='simples_nacional', codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Busca', cnpj='12345678000192', uf='RN',
        )
        unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
        )
        cls.categoria = CategoriaProduto.objects.create(
            empresa=cls.empresa, filial=cls.filial, nome='Uniformes esportivos',
        )
        cls.subcategoria = CategoriaProduto.objects.create(
            empresa=cls.empresa, filial=cls.filial, nome='Regatas',
            categoria_pai=cls.categoria,
        )
        for categoria in (cls.categoria, cls.subcategoria):
            CategoriaProdutoFilial.objects.create(categoria=categoria, filial=cls.filial)
        cls.basqueteira = Produto.objects.create(
            filial=cls.filial, unidade_medida=unidade, descricao='Basqueteira Oscar Tam G',
            categoria=cls.categoria, subcategoria=cls.subcategoria,
            codigo='REF-OSCAR', codigo_barras='7891234567890', ncm='61099000',
        )
        cls.regata = Produto.objects.create(
            filial=cls.filial, unidade_medida=unidade, descricao='Regata Azul Tam P',
            categoria=cls.categoria, subcategoria=cls.subcategoria, ncm='61099000',
        )
        cls.regata_gg = Produto.objects.create(
            filial=cls.filial, unidade_medida=unidade, descricao='Regata Azul Tam GG',
            categoria=cls.categoria, subcategoria=cls.subcategoria, ncm='61099000',
        )
        for produto in (cls.basqueteira, cls.regata, cls.regata_gg):
            ProdutoFilial.objects.create(produto=produto, filial=cls.filial)

    def request(self, **params):
        request = RequestFactory().get('/produtos/', params)
        request.user = SimpleNamespace(empresa=self.empresa)
        request.filial_ativa = self.filial
        request.session = {}
        return request

    def assert_buscas(self, query, expected):
        request = self.request(q=query)
        searches = {
            'lista_e_exportacoes': _produto_queryset_filtrado(request),
            'ajuste_rapido': AjusteRapidoEstoqueView._buscar_produtos(request)[0],
        }
        for name, queryset in searches.items():
            with self.subTest(busca=name, query=query):
                self.assertSetEqual(set(queryset.values_list('pk', flat=True)), expected)

    def test_texto_nao_pesquisa_categoria(self):
        self.assert_buscas('esportivos', set())

    def test_texto_nao_pesquisa_subcategoria(self):
        self.assert_buscas('regata', {self.regata.pk, self.regata_gg.pk})

    def test_categoria_nao_completa_termo_do_nome(self):
        self.assert_buscas('oscar esportivos', set())

    def test_subcategoria_nao_completa_termo_do_nome(self):
        self.assert_buscas('oscar regata', set())

    def test_nome_e_tamanho_continuam_pesquisaveis(self):
        self.assert_buscas('g oscar', {self.basqueteira.pk})
        self.assert_buscas('p regata', {self.regata.pk})

    def test_referencia_e_codigo_de_barras_continuam_pesquisaveis(self):
        self.assert_buscas('REF-OSCAR', {self.basqueteira.pk})
        self.assert_buscas('7891234567890', {self.basqueteira.pk})

    def test_filtros_explicitos_de_categoria_e_subcategoria_permanecem(self):
        for params in (
            {'categoria': self.categoria.pk},
            {'subcategoria': self.subcategoria.pk},
            {'categoria': self.categoria.pk, 'subcategoria': self.subcategoria.pk},
        ):
            with self.subTest(params=params):
                self.assertSetEqual(
                    set(_produto_queryset_filtrado(self.request(**params)).values_list('pk', flat=True)),
                    {self.basqueteira.pk, self.regata.pk, self.regata_gg.pk},
                )

    def test_filtro_explicito_nao_amplia_busca_textual(self):
        request = self.request(q='regata', categoria=self.categoria.pk, subcategoria=self.subcategoria.pk)
        self.assertSetEqual(
            set(_produto_queryset_filtrado(request).values_list('pk', flat=True)),
            {self.regata.pk, self.regata_gg.pk},
        )
