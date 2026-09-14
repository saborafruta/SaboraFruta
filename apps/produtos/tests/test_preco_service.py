from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial
from apps.produtos.models import (
    ItemTabelaPreco, Produto, ProdutoApresentacao, ProdutoFilial, TabelaPreco, TabelaPrecoFilial, UnidadeMedida,
)
from apps.produtos.services.preco_service import PrecoService
from apps.produtos.views.promocao import _preco_gatilho_brinde, _status_promocao, _validade_texto


class PrecoServicePromocaoVivaTests(SimpleTestCase):
    def test_desconto_combo_no_total_e_outros_tipos_preservados(self):
        for tipo,valor,esperado in [('valor','20','43.3233'),('percentual','20','39.9920'),
                                   ('preco_unitario','20','20.0000'),('preco_final','20','20.0000'),
                                   ('valor','200','0.0000')]:
            with self.subTest(tipo=tipo,valor=valor):
                faixa=SimpleNamespace(quantidade_minima=Decimal('3'),tipo_desconto=tipo,valor=Decimal(valor))
                self.assertEqual(PrecoService.preco_unitario_combo(Decimal('49.99'),faixa),Decimal(esperado))

    def produto(self, **overrides):
        dados = {
            'preco_venda': Decimal('10.00'),
            'preco_promocional': Decimal('8.00'),
            'promocao_tipo_desconto': 'preco_final',
            'promocao_valor_desconto': Decimal('8.00'),
            'promocao_inicio': date(2026, 5, 1),
            'promocao_fim': date(2026, 5, 31),
            'promocao_dias_semana': '6',
        }
        dados.update(overrides)
        return SimpleNamespace(**dados)

    def categoria(self, pk, id_externo='', categoria_pai=None):
        return SimpleNamespace(pk=pk, id_externo=id_externo, categoria_pai=categoria_pai, nome=f'Categoria {pk}')

    def regra(self, categoria=None, subcategoria=None, quantidade_minima=Decimal('1')):
        return SimpleNamespace(
            categoria=categoria,
            categoria_id=getattr(categoria, 'pk', None),
            subcategoria=subcategoria,
            subcategoria_id=getattr(subcategoria, 'pk', None),
            quantidade_minima=quantidade_minima,
        )

    def test_usa_promocao_quando_data_e_dia_casam(self):
        produto = self.produto()

        preco = PrecoService.preco_vivo_produto(produto, data=date(2026, 5, 17))

        self.assertEqual(preco, Decimal('8.00'))

    def test_volta_para_preco_normal_quando_promocao_venceu(self):
        produto = self.produto()

        preco = PrecoService.preco_vivo_produto(produto, data=date(2026, 6, 1))

        self.assertEqual(preco, Decimal('10.00'))

    def test_volta_para_preco_normal_quando_dia_nao_casa(self):
        produto = self.produto()

        preco = PrecoService.preco_vivo_produto(produto, data=date(2026, 5, 18))

        self.assertEqual(preco, Decimal('10.00'))

    def test_pode_ignorar_dia_da_semana_na_previsualizacao_comercial(self):
        produto = self.produto(promocao_dias_semana='3')

        preco = PrecoService.preco_vivo_produto(
            produto,
            data=date(2026, 5, 17),
            validar_dia_semana=False,
        )

        self.assertEqual(preco, Decimal('8.00'))

    def test_previsualizacao_comercial_ignora_promocao_com_menos_de_cinco_dias(self):
        produto = self.produto(promocao_dias_semana='0,1,2,3')

        preco = PrecoService.preco_vivo_produto(
            produto,
            data=date(2026, 5, 17),
            validar_dia_semana=False,
            minimo_dias_semana=5,
        )

        self.assertEqual(preco, Decimal('10.00'))

    def test_previsualizacao_comercial_usa_promocao_com_cinco_dias_ou_mais(self):
        produto = self.produto(promocao_dias_semana='0,1,2,3,4')

        preco = PrecoService.preco_vivo_produto(
            produto,
            data=date(2026, 5, 17),
            validar_dia_semana=False,
            minimo_dias_semana=5,
        )

        self.assertEqual(preco, Decimal('8.00'))

    def test_volta_para_preco_normal_quando_kit_ou_combo_nao_permite_promocao(self):
        produto = self.produto()

        preco = PrecoService.preco_vivo_produto(
            produto,
            usar_preco_promocional=False,
            data=date(2026, 5, 17),
        )

        self.assertEqual(preco, Decimal('10.00'))

    def test_inativacao_por_preco_zero_remove_promocao_do_calculo(self):
        produto = self.produto(preco_promocional=Decimal('0.00'))

        preco = PrecoService.preco_vivo_produto(produto, data=date(2026, 5, 17))

        self.assertEqual(preco, Decimal('10.00'))

    def test_promocao_percentual_recalcula_quando_preco_de_venda_muda(self):
        produto = self.produto(
            preco_venda=Decimal('20.00'),
            preco_promocional=Decimal('9.00'),
            promocao_tipo_desconto='percentual',
            promocao_valor_desconto=Decimal('10.00'),
        )

        preco = PrecoService.preco_vivo_produto(produto, data=date(2026, 5, 17))

        self.assertEqual(preco, Decimal('18.0000'))

    def test_promocao_valor_recalcula_quando_preco_de_venda_muda(self):
        produto = self.produto(
            preco_venda=Decimal('20.00'),
            preco_promocional=Decimal('9.00'),
            promocao_tipo_desconto='valor',
            promocao_valor_desconto=Decimal('3.50'),
        )

        preco = PrecoService.preco_vivo_produto(produto, data=date(2026, 5, 17))

        self.assertEqual(preco, Decimal('16.50'))

    def test_melhor_preco_escolhe_categoria_quando_for_menor_que_promocional(self):
        produto = self.produto(preco_venda=Decimal('10.00'), preco_promocional=Decimal('8.00'))

        with patch.object(PrecoService, 'precos_categoria_vigentes', return_value=[Decimal('7.00')]):
            preco = PrecoService.melhor_preco_produto(produto, data=date(2026, 5, 17))

        self.assertEqual(preco, Decimal('7.00'))

    def test_melhor_preco_escolhe_promocional_quando_categoria_for_maior(self):
        produto = self.produto(preco_venda=Decimal('10.00'), preco_promocional=Decimal('8.00'))

        with patch.object(PrecoService, 'precos_categoria_vigentes', return_value=[Decimal('9.00')]):
            preco = PrecoService.melhor_preco_produto(produto, data=date(2026, 5, 17))

        self.assertEqual(preco, Decimal('8.00'))

    def test_categoria_marcada_aplica_desconto_sobre_promocao_individual(self):
        produto = self.produto(preco_venda=Decimal('10.00'), preco_promocional=Decimal('8.00'))
        desconto = SimpleNamespace(permite_preco_promocional=True)

        base, origem = PrecoService._preco_base_categoria(produto, desconto=desconto, data=date(2026, 5, 17))
        preco_categoria = PrecoService.aplicar_regra_desconto(base, 'percentual', Decimal('50.00'))

        self.assertEqual(base, Decimal('8.00'))
        self.assertEqual(origem, 'promocao individual')
        self.assertEqual(preco_categoria, Decimal('4.00'))

    def test_categoria_desmarcada_aplica_desconto_sobre_preco_de_venda(self):
        produto = self.produto(preco_venda=Decimal('10.00'), preco_promocional=Decimal('8.00'))
        desconto = SimpleNamespace(permite_preco_promocional=False)

        base, origem = PrecoService._preco_base_categoria(produto, desconto=desconto, data=date(2026, 5, 17))
        preco_categoria = PrecoService.aplicar_regra_desconto(base, 'percentual', Decimal('50.00'))

        self.assertEqual(base, Decimal('10.00'))
        self.assertEqual(origem, 'preco de venda')
        self.assertEqual(preco_categoria, Decimal('5.00'))

    def test_melhor_preco_ignora_promocoes_quando_nao_permitido(self):
        produto = self.produto(preco_venda=Decimal('10.00'), preco_promocional=Decimal('8.00'))

        with patch.object(PrecoService, 'precos_categoria_vigentes', return_value=[Decimal('7.00')]):
            preco = PrecoService.melhor_preco_produto(
                produto,
                usar_promocoes=False,
                data=date(2026, 5, 17),
            )

        self.assertEqual(preco, Decimal('10.00'))

    def test_melhor_preco_detalhado_informa_categoria_quando_for_menor(self):
        produto = self.produto(preco_venda=Decimal('10.00'), preco_promocional=Decimal('7.99'))

        with patch.object(PrecoService, 'precos_categoria_vigentes_detalhados', return_value=[{
            'preco': Decimal('5.00'),
            'tipo': 'categoria',
            'origem': 'Desconto por categoria',
            'detalhe': 'Quinta da Fruta: desconto de 50%.',
        }]):
            preco = PrecoService.melhor_preco_produto_detalhado(produto, data=date(2026, 5, 17))

        self.assertEqual(preco['preco'], Decimal('5.00'))
        self.assertEqual(preco['origem'], 'Desconto por categoria')

    def test_melhor_preco_detalhado_informa_regra_individual(self):
        produto = self.produto(
            descricao='Polpa Acerola 300 ML',
            preco_venda=Decimal('10.00'),
            promocao_tipo_desconto='percentual',
            promocao_valor_desconto=Decimal('20.00'),
        )

        preco = PrecoService.melhor_preco_produto_detalhado(produto, data=date(2026, 5, 17))

        self.assertEqual(preco['preco'], Decimal('8.0000'))
        self.assertEqual(preco['origem'], 'Promoção individual')
        self.assertIn('Polpa Acerola 300 ML', preco['detalhe'])

    def test_preco_gatilho_brinde_usa_melhor_preco_com_regra_de_preview(self):
        produto = self.produto(preco_venda=Decimal('10.00'), preco_promocional=Decimal('7.99'))
        brinde = SimpleNamespace(
            produto_gatilho=produto,
            permite_preco_promocional=True,
            filial=SimpleNamespace(),
            quantidade_gatilho=Decimal('1'),
        )

        with patch.object(PrecoService, 'precos_categoria_vigentes', return_value=[Decimal('5.00')]) as mocked:
            preco = _preco_gatilho_brinde(brinde)

        self.assertEqual(preco, Decimal('5.00'))
        self.assertFalse(mocked.call_args.kwargs['validar_dia_semana'])
        self.assertEqual(mocked.call_args.kwargs['minimo_dias_semana'], 5)

    def test_desconto_categoria_pode_ignorar_dia_na_previsualizacao_comercial(self):
        desconto = SimpleNamespace(
            ativo=True,
            data_inicio=None,
            data_fim=None,
            dias_semana='3',
        )

        self.assertFalse(PrecoService.desconto_categoria_vigente(desconto, data=date(2026, 5, 17)))
        self.assertTrue(
            PrecoService.desconto_categoria_vigente(
                desconto,
                data=date(2026, 5, 17),
                validar_dia_semana=False,
            )
        )

    def test_desconto_categoria_precisa_de_cinco_dias_para_previsualizacao_comercial(self):
        desconto = SimpleNamespace(
            ativo=True,
            data_inicio=None,
            data_fim=None,
            dias_semana='0,1,2,3',
        )

        self.assertFalse(
            PrecoService.desconto_categoria_vigente(
                desconto,
                data=date(2026, 5, 17),
                validar_dia_semana=False,
                minimo_dias_semana=5,
            )
        )

        desconto.dias_semana = '0,1,2,3,4'
        self.assertTrue(
            PrecoService.desconto_categoria_vigente(
                desconto,
                data=date(2026, 5, 17),
                validar_dia_semana=False,
                minimo_dias_semana=5,
            )
        )

    def test_regra_categoria_aplica_quando_produto_esta_em_subcategoria(self):
        categoria = self.categoria(1)
        subcategoria = self.categoria(2, categoria_pai=categoria)
        produto = self.produto(categoria=None, subcategoria=subcategoria)

        self.assertTrue(PrecoService.regra_categoria_aplica(self.regra(categoria=categoria), produto, Decimal('1')))

    def test_regra_categoria_aplica_quando_categoria_replicada_tem_mesmo_id_externo(self):
        regra_categoria = self.categoria(1, id_externo='cat-polpas')
        produto_categoria = self.categoria(9, id_externo='cat-polpas')
        produto = self.produto(categoria=produto_categoria, subcategoria=None)

        self.assertTrue(PrecoService.regra_categoria_aplica(self.regra(categoria=regra_categoria), produto, Decimal('1')))

    def test_regra_categoria_aplica_quando_categoria_antiga_tem_mesmo_nome(self):
        regra_categoria = self.categoria(1)
        regra_categoria.nome = 'Polpas de Fruta'
        produto_categoria = self.categoria(9)
        produto_categoria.nome = 'polpas  de fruta'
        produto = self.produto(categoria=produto_categoria, subcategoria=None)

        self.assertTrue(PrecoService.regra_categoria_aplica(self.regra(categoria=regra_categoria), produto, Decimal('1')))

    def test_regra_categoria_aplica_quando_nome_varia_singular_plural(self):
        regra_categoria = self.categoria(1)
        regra_categoria.nome = 'Polpas de Fruta'
        produto_categoria = self.categoria(9)
        produto_categoria.nome = 'Polpa de Fruta'
        produto = self.produto(categoria=produto_categoria, subcategoria=None)

        self.assertTrue(PrecoService.regra_categoria_aplica(self.regra(categoria=regra_categoria), produto, Decimal('1')))

    def test_regra_categoria_aplica_quando_caminho_e_nome_folha_casam(self):
        categoria_pai = self.categoria(1)
        categoria_pai.nome = 'Congelados'
        regra_categoria = self.categoria(2, categoria_pai=categoria_pai)
        regra_categoria.nome = 'Polpas de Frutas'
        produto_categoria = self.categoria(9)
        produto_categoria.nome = 'Polpa de Fruta'
        produto = self.produto(categoria=produto_categoria, subcategoria=None)

        self.assertTrue(PrecoService.regra_categoria_aplica(self.regra(categoria=regra_categoria), produto, Decimal('1')))


class PromocaoListStatusTests(SimpleTestCase):
    def test_validade_sem_data_mostra_dias_quando_nao_sao_todos(self):
        self.assertEqual(_validade_texto(None, None, '3'), 'Inicio imediato, sem prazo de termino - Qui')

    def test_status_ativo_nao_muda_por_dia_da_semana(self):
        promocao = SimpleNamespace(ativo=True, data_inicio=None, data_fim=None, dias_semana='3')

        status = _status_promocao(promocao, date(2026, 5, 17))

        self.assertEqual(status['texto'], 'Ativo')
        self.assertEqual(status['estado'], 'ativas')


class PrecoClienteDetalhadoApresentacaoTests(TestCase):
    """Apresentacao conectada em PrecoService.preco_cliente_detalhado --
    o preco de apresentacao e' independente (regra 5.8), so' a tabela de
    preco do cliente pode ser mais especifica que ele."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Preco Apresentacao LTDA', cnpj='71345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL, codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial Preco Apresentacao', cnpj='71345678000192', uf='RN',
        )
        cls.un = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='UN', descricao='Unidade')
        cls.cx = UnidadeMedida.objects.create(empresa=cls.empresa, sigla='CX', descricao='Caixa')
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.un, descricao='Produto Teste',
            codigo='1001', ncm='39235000', preco_venda=Decimal('10.00'),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)
        cls.apresentacao = ProdutoApresentacao.objects.create(
            produto=cls.produto, unidade=cls.cx, descricao='Caixa 10', fator_conversao=10,
            preco_venda=Decimal('90.00'),
        )

    def test_usa_preco_proprio_da_apresentacao_sem_tabela_de_cliente(self):
        resultado = PrecoService.preco_cliente_detalhado(
            self.produto, Decimal('10'), filial=self.filial, apresentacao=self.apresentacao,
        )
        # 90 / 10 = 9.00 por unidade base -- nao 10 (preco do produto na
        # unidade base), que daria a impressao errada de que a caixa nao
        # tem desconto de atacado nenhum.
        self.assertEqual(resultado['preco'], Decimal('9.0000'))
        self.assertEqual(resultado['tipo'], 'apresentacao')
        self.assertEqual(resultado['apresentacao_id'], self.apresentacao.pk)

    def test_item_de_tabela_especifico_da_apresentacao_tem_prioridade(self):
        tabela = TabelaPreco.objects.create(filial=self.filial, descricao='Atacado')
        TabelaPrecoFilial.objects.create(tabela=tabela, filial=self.filial)
        ItemTabelaPreco.objects.create(
            tabela=tabela, produto=self.produto, apresentacao=self.apresentacao,
            preco_unitario=Decimal('8.50'), quantidade_minima=0,
        )
        cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='J', razao_social='Cliente Atacado LTDA', tabela_preco=tabela,
        )
        ClienteFilial.objects.create(cliente=cliente, filial=self.filial)

        resultado = PrecoService.preco_cliente_detalhado(
            self.produto, Decimal('10'), cliente=cliente, filial=self.filial, apresentacao=self.apresentacao,
        )
        self.assertEqual(resultado['preco'], Decimal('8.50'))
        self.assertEqual(resultado['tipo'], 'tabela_cliente_apresentacao')
        self.assertEqual(resultado['apresentacao_id'], self.apresentacao.pk)

    def test_sem_preco_proprio_e_sem_item_de_tabela_cai_para_cascata_padrao(self):
        apresentacao_sem_preco = ProdutoApresentacao.objects.create(
            produto=self.produto, unidade=self.cx, descricao='Caixa Avulsa', fator_conversao=20,
        )
        resultado = PrecoService.preco_cliente_detalhado(
            self.produto, Decimal('10'), filial=self.filial, apresentacao=apresentacao_sem_preco,
        )
        # Sem preco_venda proprio e sem item de tabela: cai pro preco normal
        # do produto (cascata padrao), nao fica em zero.
        self.assertEqual(resultado['preco'], Decimal('10.00'))
        self.assertEqual(resultado['tipo'], 'normal')

    def test_venda_sem_apresentacao_ignora_item_de_tabela_scoped_a_apresentacao(self):
        # Regressao do fix: antes de adicionar apresentacao__isnull=True no
        # filtro da cascata padrao, um item de tabela criado PARA uma
        # apresentacao especifica podia vazar pra uma venda comum (sem
        # apresentacao) do mesmo produto/tabela/faixa.
        tabela = TabelaPreco.objects.create(filial=self.filial, descricao='Atacado')
        TabelaPrecoFilial.objects.create(tabela=tabela, filial=self.filial)
        ItemTabelaPreco.objects.create(
            tabela=tabela, produto=self.produto, apresentacao=self.apresentacao,
            preco_unitario=Decimal('8.50'), quantidade_minima=0,
        )
        cliente = Cliente.objects.create(
            filial=self.filial, tipo_pessoa='J', razao_social='Cliente Atacado LTDA', tabela_preco=tabela,
        )
        ClienteFilial.objects.create(cliente=cliente, filial=self.filial)

        resultado = PrecoService.preco_cliente_detalhado(
            self.produto, Decimal('10'), cliente=cliente, filial=self.filial,
        )  # sem apresentacao
        self.assertEqual(resultado['preco'], self.produto.preco_venda)
        self.assertEqual(resultado['tipo'], 'normal')
