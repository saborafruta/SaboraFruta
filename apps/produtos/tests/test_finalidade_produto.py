import importlib
from decimal import Decimal

from django.apps import apps as django_apps
from django.db import connection
from django.test import TestCase

from apps.core.models import Empresa, Filial
from apps.pdv.services.produto_vendavel_service import ProdutoVendavelService
from apps.produtos.forms.produto import ProdutoForm
from apps.produtos.models import (
    CategoriaProduto, CategoriaProdutoFilial, Produto, ProdutoFilial,
    UnidadeMedida,
)


class FinalidadeProdutoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Finalidade Produto LTDA', nome_fantasia='Finalidade',
            cnpj='72345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Finalidade Filial',
            nome_fantasia='Finalidade Filial', cnpj='72345678000192', uf='RN',
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )

    def criar_produto(self, descricao, finalidade, permite_venda):
        produto = Produto.objects.create(
            filial=self.filial,
            unidade_medida=self.unidade,
            descricao=descricao,
            ncm='34022000',
            codigo_barras=f'789{Produto.objects.count():010d}',
            preco_custo=Decimal('2.00'),
            preco_venda=Decimal('5.00'),
            finalidade=finalidade,
            permite_venda=permite_venda,
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        return produto

    def test_queryset_vendaveis_exclui_insumo_de_uso_interno(self):
        insumo = self.criar_produto(
            'Essência industrial', Produto.Finalidade.MATERIA_PRIMA, False,
        )
        acabado = self.criar_produto(
            'Desinfetante 2L', Produto.Finalidade.ACABADO, True,
        )

        ids = set(
            Produto.objects.for_filial(self.filial).vendaveis()
            .values_list('pk', flat=True)
        )

        self.assertNotIn(insumo.pk, ids)
        self.assertIn(acabado.pk, ids)

    def test_servico_comercial_bloqueia_insumo_mesmo_com_preco(self):
        insumo = self.criar_produto(
            'Corante industrial', Produto.Finalidade.MATERIA_PRIMA, False,
        )

        contrato = ProdutoVendavelService.consultar(
            produto=insumo, filial=self.filial,
        )

        self.assertFalse(contrato['pode_vender'])
        self.assertIn(
            'produto_nao_vendavel',
            {item['codigo'] for item in contrato['bloqueios']},
        )

    def test_formulario_distingue_checkbox_desmarcado_de_cliente_antigo(self):
        formulario_atual = ProdutoForm({
            'finalidade': Produto.Finalidade.MATERIA_PRIMA,
            'permite_venda_presente': '1',
        })
        formulario_antigo = ProdutoForm({
            'tipo_produto': Produto.TipoProduto.SERVICO,
        })

        self.assertFalse(formulario_atual['permite_venda'].value())
        self.assertEqual(
            formulario_antigo.data['finalidade'], Produto.Finalidade.SERVICO,
        )
        self.assertEqual(formulario_antigo.data['permite_venda'], 'on')

    def test_migracao_cria_categoria_insumos_disponivel_na_filial(self):
        migracao = importlib.import_module(
            'apps.produtos.migrations.0046_produto_finalidade_vendavel'
        )
        migracao.criar_categoria_insumos(django_apps, connection.schema_editor())

        categoria = CategoriaProduto.objects.get(
            empresa=self.empresa, nome='Insumos', categoria_pai__isnull=True,
        )
        self.assertTrue(categoria.ativo)
        self.assertTrue(CategoriaProdutoFilial.objects.filter(
            categoria=categoria, filial=self.filial, ativo=True,
        ).exists())
