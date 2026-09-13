from unittest.mock import patch

from django.test import RequestFactory, TestCase

from apps.core.models import Empresa, Filial
from apps.produtos.forms import ProdutoForm
from apps.produtos.models import (
    Produto,
    ProdutoCodigoBarras,
    ProdutoFornecedorEquivalencia,
    UnidadeMedida,
)
from apps.produtos.services.codigo_barras_service import (
    calcular_digito_verificador_ean13,
    codigo_barras_em_uso,
    ean13_valido,
    gerar_codigo_barras_unico,
)
from apps.produtos.views.produto import ProdutoGerarCodigoBarrasView


class CodigoBarrasProdutoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Codigo de Barras LTDA',
            nome_fantasia='Empresa Codigo de Barras',
            cnpj='71345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Codigo de Barras',
            nome_fantasia='Filial Codigo de Barras',
            cnpj='71345678000192',
            uf='RN',
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa,
            sigla='UN',
            descricao='Unidade',
        )

    def criar_produto(self, **overrides):
        dados = {
            'filial': self.filial,
            'unidade_medida': self.unidade,
            'descricao': 'Produto teste',
            'ncm': '12345678',
        }
        dados.update(overrides)
        return Produto.objects.create(**dados)

    def test_calcula_e_valida_ean13(self):
        self.assertEqual(calcular_digito_verificador_ean13('789123456789'), '5')
        self.assertTrue(ean13_valido('7891234567895'))
        self.assertFalse(ean13_valido('7891234567890'))

    def test_gerador_tenta_novamente_quando_codigo_ja_existe(self):
        corpo_existente = '290000000001'
        codigo_existente = corpo_existente + calcular_digito_verificador_ean13(corpo_existente)
        self.criar_produto(codigo_barras=codigo_existente)

        with patch(
            'apps.produtos.services.codigo_barras_service.secrets.randbelow',
            side_effect=[1, 2],
        ):
            codigo = gerar_codigo_barras_unico(empresa=self.empresa)

        self.assertNotEqual(codigo, codigo_existente)
        self.assertTrue(codigo.startswith('290'))
        self.assertTrue(ean13_valido(codigo))
        self.assertFalse(codigo_barras_em_uso(codigo, empresa=self.empresa))

    def test_verifica_codigo_principal_extra_e_equivalencias(self):
        produto = self.criar_produto(
            codigo_barras='2900000000018',
            codigos_barras_extras=['2900000000025'],
        )
        ProdutoCodigoBarras.objects.create(produto=produto, ean='2900000000032')
        ProdutoFornecedorEquivalencia.objects.create(
            produto=produto,
            ean_utilizado='2900000000049',
        )

        for codigo in (
            '2900000000018',
            '2900000000025',
            '2900000000032',
            '2900000000049',
        ):
            with self.subTest(codigo=codigo):
                self.assertTrue(codigo_barras_em_uso(codigo, empresa=self.empresa))
                self.assertFalse(
                    codigo_barras_em_uso(
                        codigo,
                        empresa=self.empresa,
                        produto_id=produto.pk,
                    )
                )

    def test_formulario_rejeita_codigo_de_outro_produto(self):
        self.criar_produto(codigo_barras='2900000000018')
        form = ProdutoForm(
            data={'codigo_barras': '2900000000018'},
            empresa=self.empresa,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('ja pertence a outro produto', form.errors['codigo_barras'][0])

    @patch(
        'apps.produtos.views.produto.gerar_codigo_barras_unico',
        return_value='2901234567896',
    )
    def test_endpoint_retorna_codigo_gerado(self, gerar_codigo):
        request = RequestFactory().post(
            '/produtos/gerar-codigo-barras/',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        request.user = type('UsuarioPermitido', (), {
            'is_authenticated': True,
            'empresa': self.empresa,
            'tem_permissao': lambda self, modulo, acao: True,
        })()

        response = ProdutoGerarCodigoBarrasView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                'ok': True,
                'codigo_barras': '2901234567896',
                'formato': 'EAN-13 interno',
            },
        )
        gerar_codigo.assert_called_once_with(empresa=self.empresa)
