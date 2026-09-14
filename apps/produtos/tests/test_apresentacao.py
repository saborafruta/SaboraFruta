from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.core.models import Empresa, Filial
from apps.core.services.exceptions import DadosInvalidosError
from apps.produtos.models import (
    Produto,
    ProdutoApresentacao,
    ProdutoCodigoBarras,
    UnidadeMedida,
)
from apps.produtos.services.apresentacao_service import ApresentacaoService


class ProdutoApresentacaoTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Apresentacoes LTDA',
            nome_fantasia='Empresa Apresentacoes',
            cnpj='71345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Apresentacoes',
            nome_fantasia='Filial Apresentacoes',
            cnpj='71345678000192',
            uf='RN',
        )
        cls.un = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='UN', descricao='Unidade')
        cls.pct = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='PCT', descricao='Pacote')
        cls.cx = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='CX', descricao='Caixa')

    def criar_produto(self, **overrides):
        dados = {
            'filial': self.filial,
            'unidade_medida': self.un,
            'descricao': 'Embalagem Pote 500ml',
            'codigo': '1001',
            'ncm': '39235000',
        }
        dados.update(overrides)
        return Produto.objects.create(**dados)


class ProdutoApresentacaoModelTests(ProdutoApresentacaoTestBase):
    def test_fator_conversao_zero_e_invalido(self):
        produto = self.criar_produto()
        apresentacao = ProdutoApresentacao(
            produto=produto, unidade=self.pct, descricao='Pacote invalido', fator_conversao=0,
        )
        with self.assertRaises(ValidationError):
            apresentacao.full_clean()

    def test_fator_conversao_negativo_e_invalido(self):
        produto = self.criar_produto()
        apresentacao = ProdutoApresentacao(
            produto=produto, unidade=self.pct, descricao='Pacote invalido', fator_conversao=-10,
        )
        with self.assertRaises(ValidationError):
            apresentacao.full_clean()

    def test_apenas_uma_apresentacao_principal_venda_por_produto(self):
        produto = self.criar_produto()
        ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.un, descricao='Unidade', fator_conversao=1, principal_venda=True,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProdutoApresentacao.objects.create(
                    produto=produto, unidade=self.cx, descricao='Caixa 1.000',
                    fator_conversao=1000, principal_venda=True,
                )

    def test_apenas_uma_apresentacao_principal_compra_por_produto(self):
        produto = self.criar_produto()
        ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 1.000', fator_conversao=1000, principal_compra=True,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProdutoApresentacao.objects.create(
                    produto=produto, unidade=self.un, descricao='Unidade',
                    fator_conversao=1, principal_compra=True,
                )

    def test_principal_venda_e_principal_compra_sao_independentes(self):
        produto = self.criar_produto()
        # Vende por padrao em UN, compra por padrao em CX -- mesma apresentacao
        # nao precisa acumular as duas flags.
        ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.un, descricao='Unidade', fator_conversao=1, principal_venda=True,
        )
        # Nao deve levantar: as constraints sao independentes por flag.
        ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 1.000', fator_conversao=1000, principal_compra=True,
        )

    def test_duas_apresentacoes_principais_em_produtos_diferentes_sao_permitidas(self):
        produto_a = self.criar_produto(codigo='1001')
        produto_b = self.criar_produto(codigo='1002', descricao='Outro produto')
        ProdutoApresentacao.objects.create(
            produto=produto_a, unidade=self.un, descricao='Unidade', fator_conversao=1, principal_venda=True,
        )
        # Nao deve levantar: constraint e por produto, nao global.
        ProdutoApresentacao.objects.create(
            produto=produto_b, unidade=self.un, descricao='Unidade', fator_conversao=1, principal_venda=True,
        )

    def test_nao_permite_duas_apresentacoes_ativas_com_mesma_unidade_e_fator(self):
        produto = self.criar_produto()
        ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.pct, descricao='Pacote 100', fator_conversao=100,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProdutoApresentacao.objects.create(
                    produto=produto, unidade=self.pct, descricao='Pacote 100 (duplicado)', fator_conversao=100,
                )

    def test_permite_duas_apresentacoes_com_mesma_unidade_e_fator_se_uma_estiver_inativa(self):
        produto = self.criar_produto()
        ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.pct, descricao='Pacote 100 antigo', fator_conversao=100, ativo=False,
        )
        # Nao deve levantar: a constraint so vale entre apresentacoes ativas.
        ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.pct, descricao='Pacote 100 novo', fator_conversao=100,
        )

    def test_check_constraint_fator_conversao_no_banco_pega_bypass_do_clean(self):
        produto = self.criar_produto()
        apresentacao = ProdutoApresentacao(
            produto=produto, unidade=self.pct, descricao='Pacote invalido', fator_conversao=100,
        )
        apresentacao.save()
        # Simula quem grava direto no banco sem passar por full_clean():
        # a CheckConstraint tem que barrar mesmo assim.
        apresentacao.fator_conversao = 0
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                apresentacao.save(update_fields=['fator_conversao'])

    def test_unique_together_produto_unidade_descricao(self):
        produto = self.criar_produto()
        ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.pct, descricao='Pacote 100', fator_conversao=100,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProdutoApresentacao.objects.create(
                    produto=produto, unidade=self.pct, descricao='Pacote 100', fator_conversao=200,
                )

    def test_cubagem_calculada_a_partir_das_dimensoes_em_cm(self):
        produto = self.criar_produto()  # unidade_dimensao default = CM
        caixa = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 1.000', fator_conversao=1000,
            largura=Decimal('40'), altura=Decimal('30'), profundidade=Decimal('25'),
        )
        # 40cm x 30cm x 25cm = 30000 cm3 = 0.03 m3
        self.assertEqual(caixa.cubagem, Decimal('0.03'))

    def test_cubagem_zero_quando_falta_alguma_dimensao(self):
        produto = self.criar_produto()
        caixa = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 1.000', fator_conversao=1000,
            largura=Decimal('40'), altura=Decimal('30'),
        )
        self.assertEqual(caixa.cubagem, 0)

    def test_permite_venda_compra_estoque_default_true(self):
        produto = self.criar_produto()
        apresentacao = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.un, descricao='Unidade', fator_conversao=1,
        )
        self.assertTrue(apresentacao.permite_venda)
        self.assertTrue(apresentacao.permite_compra)
        self.assertTrue(apresentacao.permite_estoque)

    def test_apresentacao_pode_ser_so_informativa(self):
        # Ex: "Display com 6 caixas" -- existe pra composicao/exibicao, mas
        # nao deve ser usada em venda/compra/baixa de estoque direta.
        produto = self.criar_produto()
        display = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Display 6 Caixas', fator_conversao=6000,
            permite_venda=False, permite_compra=False, permite_estoque=False,
        )
        self.assertFalse(display.permite_venda)
        self.assertFalse(display.permite_compra)
        self.assertFalse(display.permite_estoque)

    def test_converter_para_base_e_de_base(self):
        produto = self.criar_produto()
        caixa = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 1.000', fator_conversao=1000,
        )
        self.assertEqual(caixa.converter_para_base(Decimal('3')), Decimal('3000'))
        self.assertEqual(caixa.converter_de_base(Decimal('3000')), Decimal('3'))


class ProdutoCodigoBarrasApresentacaoTests(ProdutoApresentacaoTestBase):
    def test_ean_ativo_duplicado_no_mesmo_produto_e_invalido(self):
        produto = self.criar_produto()
        caixa = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 1.000', fator_conversao=1000,
        )
        pacote = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.pct, descricao='Pacote 500', fator_conversao=500,
        )
        ProdutoCodigoBarras.objects.create(produto=produto, apresentacao=caixa, ean='7890000000013')

        duplicado = ProdutoCodigoBarras(produto=produto, apresentacao=pacote, ean='7890000000013')
        with self.assertRaises(ValidationError):
            duplicado.full_clean()

    def test_ean_duplicado_mas_inativo_nao_bloqueia(self):
        produto = self.criar_produto()
        caixa = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 1.000', fator_conversao=1000,
        )
        pacote = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.pct, descricao='Pacote 500', fator_conversao=500,
        )
        ProdutoCodigoBarras.objects.create(
            produto=produto, apresentacao=caixa, ean='7890000000013', ativo=False,
        )

        novo = ProdutoCodigoBarras(produto=produto, apresentacao=pacote, ean='7890000000013')
        novo.full_clean()  # nao deve levantar

    def test_mesmo_ean_em_produtos_diferentes_e_permitido(self):
        produto_a = self.criar_produto(codigo='1001')
        produto_b = self.criar_produto(codigo='1002', descricao='Outro produto')
        ProdutoCodigoBarras.objects.create(produto=produto_a, ean='7890000000013')

        outro = ProdutoCodigoBarras(produto=produto_b, ean='7890000000013')
        outro.full_clean()  # escopo e por produto, nao global


class ApresentacaoServiceTests(ProdutoApresentacaoTestBase):
    def test_calcular_fator_encadeado_multiplica_os_elos(self):
        # "1 CX = 12 PCT" e "1 PCT = 10 UN" -> fator absoluto da caixa e 120.
        fator = ApresentacaoService.calcular_fator_encadeado(12, 10)
        self.assertEqual(fator, Decimal('120'))

    def test_calcular_fator_encadeado_exige_ao_menos_um_elo(self):
        with self.assertRaises(DadosInvalidosError):
            ApresentacaoService.calcular_fator_encadeado()

    def test_calcular_fator_encadeado_rejeita_elo_invalido(self):
        with self.assertRaises(DadosInvalidosError):
            ApresentacaoService.calcular_fator_encadeado(12, 'abc')

    def test_converter_para_base_e_para_apresentacao(self):
        produto = self.criar_produto()
        caixa = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 1.000', fator_conversao=1000,
        )
        self.assertEqual(
            ApresentacaoService.converter(caixa, 3, para='base'), Decimal('3000'),
        )
        self.assertEqual(
            ApresentacaoService.converter(caixa, 3000, para='apresentacao'), Decimal('3'),
        )

    def test_converter_parametro_para_invalido(self):
        produto = self.criar_produto()
        caixa = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 1.000', fator_conversao=1000,
        )
        with self.assertRaises(DadosInvalidosError):
            ApresentacaoService.converter(caixa, 1, para='invalido')

    def test_converter_entre_apresentacoes_do_mesmo_produto(self):
        produto = self.criar_produto()
        caixa = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 1.000', fator_conversao=1000,
        )
        pacote100 = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.pct, descricao='Pacote 100', fator_conversao=100,
        )
        resultado = ApresentacaoService.converter_entre_apresentacoes(caixa, pacote100, 2)
        self.assertEqual(resultado, Decimal('20'))  # 2 CX = 2000 UN = 20 PCT100

    def test_converter_arredonda_pelo_casas_decimais_da_unidade_destino(self):
        # Regra 5.10: o arredondamento usa ROUND_HALF_UP, centralizado em
        # services/conversao.py, respeitando casas_decimais da unidade de
        # destino -- nao o numero de casas "naturais" da divisao.
        un_contagem = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='UNC', descricao='Unidade (contagem)', casas_decimais=0,
        )
        produto = self.criar_produto()
        pacote3 = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.pct, descricao='Pacote com 3', fator_conversao=3,
        )
        unidade_avulsa = ProdutoApresentacao.objects.create(
            produto=produto, unidade=un_contagem, descricao='Unidade avulsa', fator_conversao=1,
        )
        # 1 pacote com 3 = 3 UN base; convertendo pra "unidade avulsa"
        # (fator 1) da exatamente 3 -- sem fracao, sem precisar arredondar.
        # Forcamos uma fracao real usando 2 pacotes de 3 dividido por um
        # fator que nao fecha redondo.
        resultado = ApresentacaoService.converter_entre_apresentacoes(pacote3, unidade_avulsa, Decimal('2.5'))
        # 2.5 pacotes de 3 = 7.5 UN base; unidade_avulsa tem casas_decimais=0
        # -> ROUND_HALF_UP arredonda 7.5 para 8.
        self.assertEqual(resultado, Decimal('8'))

    def test_converter_entre_apresentacoes_de_produtos_diferentes_falha(self):
        produto_a = self.criar_produto(codigo='1001')
        produto_b = self.criar_produto(codigo='1002', descricao='Outro produto')
        caixa = ProdutoApresentacao.objects.create(
            produto=produto_a, unidade=self.cx, descricao='Caixa 1.000', fator_conversao=1000,
        )
        unidade = ProdutoApresentacao.objects.create(
            produto=produto_b, unidade=self.un, descricao='Unidade', fator_conversao=1,
        )
        with self.assertRaises(DadosInvalidosError):
            ApresentacaoService.converter_entre_apresentacoes(caixa, unidade, 1)

    def test_apresentacao_principal_venda_retorna_a_marcada(self):
        produto = self.criar_produto()
        ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.pct, descricao='Pacote 100', fator_conversao=100,
        )
        principal = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.un, descricao='Unidade', fator_conversao=1, principal_venda=True,
        )
        self.assertEqual(ApresentacaoService.apresentacao_principal_venda(produto), principal)

    def test_apresentacao_principal_venda_ignora_inativas(self):
        produto = self.criar_produto()
        ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.un, descricao='Unidade', fator_conversao=1,
            principal_venda=True, ativo=False,
        )
        self.assertIsNone(ApresentacaoService.apresentacao_principal_venda(produto))

    def test_apresentacao_principal_compra_retorna_a_marcada(self):
        produto = self.criar_produto()
        ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.un, descricao='Unidade', fator_conversao=1,
        )
        principal = ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 1.000', fator_conversao=1000, principal_compra=True,
        )
        self.assertEqual(ApresentacaoService.apresentacao_principal_compra(produto), principal)

    def test_apresentacao_principal_compra_ignora_inativas(self):
        produto = self.criar_produto()
        ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 1.000', fator_conversao=1000,
            principal_compra=True, ativo=False,
        )
        self.assertIsNone(ApresentacaoService.apresentacao_principal_compra(produto))

    def test_listar_ativas_ordena_por_fator_e_ignora_inativas(self):
        produto = self.criar_produto()
        ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.cx, descricao='Caixa 1.000', fator_conversao=1000,
        )
        ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.un, descricao='Unidade', fator_conversao=1,
        )
        ProdutoApresentacao.objects.create(
            produto=produto, unidade=self.pct, descricao='Pacote 100', fator_conversao=100, ativo=False,
        )
        fatores = list(
            ApresentacaoService.listar_ativas(produto).values_list('fator_conversao', flat=True)
        )
        self.assertEqual(fatores, [Decimal('1'), Decimal('1000')])
