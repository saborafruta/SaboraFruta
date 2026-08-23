"""
Margens — o que sobrou de cada pedido e de cada produto.

É a única tela do vertical com os dois lados da conta juntos, e por isso a
mais fácil de estragar em silêncio: um erro de receita não parece erro, só
parece um produto que dá menos lucro do que se imaginava.

O que os testes cercam:

  · FRETE na receita — ele é repasse e o custo dele não está do outro lado,
    então somá-lo inventaria margem que não existe;
  · DESCONTO ignorado ou jogado inteiro numa ordem só — o desconto é do
    pedido e a margem é medida por ordem;
  · receita contada pelo item inteiro quando ele virou duas ordens, o que
    dobraria o faturamento do pedido;
  · pedido SEM PREÇO tratado como prejuízo de −100%, afundando a média de
    todo mundo por causa de uma amostra.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.moda.models import (
    EtapaOrdem, FichaTecnica, ItemPedidoProducao, MaterialFicha, Operacao,
    OperacaoRoteiro, OrdemProducao, PedidoProducao, ProdutoModa, RegistroCorte,
    Roteiro,
)
from apps.moda.services.margem import MARGEM_MAGRA, MargemService


class MargemBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Margem LTDA', nome_fantasia='Margem',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Margem LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Time do Bairro',
            cpf_cnpj='12345678901',
        )

    def setUp(self):
        self.produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        self._n = 0

    # ── Montagem ─────────────────────────────────────────────────────────

    def _ficha(self, custo='20', produto=None):
        ficha = FichaTecnica.objects.create(
            filial=self.filial, produto=produto or self.produto,
        )
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha Dry', unidade=MaterialFicha.Unidade.METRO,
            consumo=Decimal('1'), custo_unitario=Decimal(custo),
        )
        return ficha

    def _roteiro(self, minutos=10, custo_hora=60, produto=None):
        produto = produto or self.produto
        roteiro = Roteiro.objects.create(filial=self.filial, produto=produto)
        operacao = Operacao.objects.create(
            filial=self.filial, nome=f'Costura {produto.codigo}',
            setor=Operacao.Setor.COSTURA, tempo_padrao=Decimal(minutos),
            tipo_custo=Operacao.TipoCusto.POR_HORA, custo=Decimal(custo_hora),
        )
        OperacaoRoteiro.objects.create(
            roteiro=roteiro, operacao=operacao, sequencia=10,
        )
        return roteiro

    def _pedido(self, desconto='0', frete='0'):
        self._n += 1
        return PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=self._n,
            desconto=Decimal(desconto), frete=Decimal(frete),
        )

    def _ordem(self, pedido, preco='80', quantidade=100, produto=None,
               ordem_quantidade=None):
        self._n += 1
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto or self.produto, descricao='Camisa',
            quantidade=quantidade, valor_unitario=Decimal(preco),
        )
        return OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            status=OrdemProducao.Status.CONCLUIDA,
            numero=f'OP-{self._n:04d}', ano=2026, sequencial=self._n,
            quantidade=ordem_quantidade or quantidade,
        )

    def _terminar(self, ordem, produzido=None, dias_atras=1):
        EtapaOrdem.objects.create(
            ordem=ordem, etapa=EtapaOrdem.Etapa.COSTURA, sequencia=6,
            status=EtapaOrdem.Status.CONCLUIDA,
            quantidade_produzida=produzido or ordem.quantidade,
            data_conclusao=timezone.localdate() - timedelta(days=dias_atras),
        )

    def _corte(self, ordem, consumo):
        self._n += 1
        return RegistroCorte.objects.create(
            filial=self.filial, ordem=ordem, numero=self._n,
            status=RegistroCorte.Status.CORTADO, quantidade=ordem.quantidade,
            data=timezone.localdate(), consumo_real=Decimal(consumo),
        )

    def _painel(self, dias=30):
        return MargemService.painel(self.filial, dias)

    def _linha(self):
        return self._painel()['linhas'][0]


class ReceitaTests(MargemBase):
    """O lado que a tela de Custos não tinha."""

    def test_receita_e_preco_vezes_a_quantidade_da_ordem(self):
        """100 peças a R$ 80 são R$ 8.000 de receita."""
        self._ficha(custo='20')
        pedido = self._pedido()
        ordem = self._ordem(pedido, preco='80', quantidade=100)
        self._terminar(ordem)

        linha = self._linha()

        self.assertEqual(linha['receita'], Decimal('8000.00'))
        # Custo previsto 20/peça; sem corte medido o real bate com ele.
        self.assertEqual(linha['margem'], Decimal('6000.00'))
        self.assertEqual(linha['margem_pct'], Decimal('75.0'))

    def test_o_frete_fica_de_fora_da_receita(self):
        """
        Frete é repasse, e o custo dele não está do outro lado da conta:
        somá-lo inventaria margem que não existe.
        """
        self._ficha(custo='20')
        pedido = self._pedido(frete='500')
        ordem = self._ordem(pedido, preco='80', quantidade=100)
        self._terminar(ordem)

        self.assertEqual(self._linha()['receita'], Decimal('8000.00'))

    def test_o_desconto_do_pedido_entra_na_receita(self):
        """Desconto é redução de receita de verdade, diferente do frete."""
        self._ficha(custo='20')
        pedido = self._pedido(desconto='800')
        ordem = self._ordem(pedido, preco='80', quantidade=100)
        self._terminar(ordem)

        linha = self._linha()

        self.assertEqual(linha['receita_bruta'], Decimal('8000.00'))
        self.assertEqual(linha['desconto'], Decimal('800.00'))
        self.assertEqual(linha['receita'], Decimal('7200.00'))

    def test_o_desconto_e_rateado_entre_as_ordens_do_pedido(self):
        """
        Jogar o desconto inteiro na primeira faria uma ordem parecer péssima
        e a outra ótima, sem que nada disso tenha acontecido na fábrica.
        """
        self._ficha(custo='20')
        pedido = self._pedido(desconto='1000')
        grande = self._ordem(pedido, preco='80', quantidade=75)
        pequena = self._ordem(pedido, preco='80', quantidade=25)
        self._terminar(grande)
        self._terminar(pequena)

        por_numero = {l['numero']: l for l in self._painel()['linhas']}
        descontos = sorted(l['desconto'] for l in por_numero.values())

        self.assertEqual(descontos, [Decimal('250.00'), Decimal('750.00')])

    def test_item_partido_em_duas_ordens_divide_a_receita(self):
        """
        Somar o item inteiro em cada ordem dobraria o faturamento do
        pedido. A receita segue a quantidade DA ORDEM.
        """
        self._ficha(custo='20')
        pedido = self._pedido()
        ordem = self._ordem(pedido, preco='80', quantidade=100,
                            ordem_quantidade=40)
        self._terminar(ordem, produzido=40)

        self.assertEqual(self._linha()['receita'], Decimal('3200.00'))

    def test_pedido_sem_preco_nao_e_prejuizo(self):
        """
        Amostra e reposição de garantia entram com receita zero. Tratá-las
        como −100% afundaria a média de todo mundo.
        """
        self._ficha(custo='20')
        pedido = self._pedido()
        ordem = self._ordem(pedido, preco='0', quantidade=100)
        self._terminar(ordem)

        painel = self._painel()
        linha = painel['linhas'][0]

        self.assertTrue(linha['sem_preco'])
        self.assertFalse(linha['prejuizo'])
        self.assertIsNone(painel['resumo']['margem_pct'])
        self.assertEqual(painel['resumo']['sem_preco'], 1)

    def test_o_sem_preco_nao_entra_na_media(self):
        self._ficha(custo='20')
        pago = self._pedido()
        amostra = self._pedido()
        self._terminar(self._ordem(pago, preco='80', quantidade=100))
        self._terminar(self._ordem(amostra, preco='0', quantidade=10))

        resumo = self._painel()['resumo']

        self.assertEqual(resumo['receita'], Decimal('8000.00'))
        self.assertEqual(resumo['custo'], Decimal('2000.00'))
        self.assertEqual(resumo['margem_pct'], Decimal('75.0'))


class MargemRealTests(MargemBase):
    """Prevista e real lado a lado — a diferença é o que a fábrica comeu."""

    def test_o_custo_real_derruba_a_margem_prevista(self):
        """
        Previsto 20/peça em 100 peças = 2.000. Gastou 130 m a R$ 20 = 2.600.
        Sobre 8.000 de receita: prevista 75%, real 67,5%.
        """
        self._ficha(custo='20')
        pedido = self._pedido()
        ordem = self._ordem(pedido, preco='80', quantidade=100)
        self._terminar(ordem)
        self._corte(ordem, consumo='130')

        linha = self._linha()

        self.assertEqual(linha['margem_prevista_pct'], Decimal('75.0'))
        self.assertEqual(linha['margem_pct'], Decimal('67.5'))
        self.assertEqual(linha['erosao'], Decimal('600.00'))

    def test_um_pedido_bem_precificado_pode_dar_prejuizo(self):
        """
        É o caso que justifica as duas colunas: o preço cobria o custo da
        ficha e a fábrica comeu a diferença.
        """
        self._ficha(custo='20')
        pedido = self._pedido()
        ordem = self._ordem(pedido, preco='25', quantidade=100)
        self._terminar(ordem)
        self._corte(ordem, consumo='200')

        linha = self._linha()

        self.assertGreater(linha['margem_prevista'], Decimal('0'))
        self.assertLess(linha['margem'], Decimal('0'))
        self.assertTrue(linha['prejuizo'])

    def test_margem_fina_e_marcada_sem_ser_prejuizo(self):
        self._ficha(custo='20')
        self._roteiro(minutos=10, custo_hora=60)
        pedido = self._pedido()
        # 30 de custo em 33 de preco -> ~9% de margem.
        ordem = self._ordem(pedido, preco='33', quantidade=100)
        self._terminar(ordem)

        linha = self._linha()

        self.assertTrue(linha['magra'])
        self.assertFalse(linha['prejuizo'])
        self.assertLess(linha['margem_pct'], MARGEM_MAGRA)

    def test_o_custo_e_o_mesmo_da_tela_de_custos(self):
        """
        Dois números diferentes para a mesma ordem fariam as duas telas
        perderem a confiança de uma vez.
        """
        from apps.moda.services.custo_real import CustoRealService

        self._ficha(custo='20')
        pedido = self._pedido()
        ordem = self._ordem(pedido, preco='80', quantidade=100)
        self._terminar(ordem)
        self._corte(ordem, consumo='130')

        margem = self._linha()
        custos = CustoRealService.painel(self.filial, 30)['linhas'][0]

        self.assertEqual(margem['real'], custos['real'])
        self.assertEqual(margem['previsto'], custos['previsto'])


class AgrupamentoTests(MargemBase):

    def test_agrupa_por_pedido(self):
        self._ficha(custo='20')
        pedido = self._pedido()
        self._terminar(self._ordem(pedido, preco='80', quantidade=60))
        self._terminar(self._ordem(pedido, preco='80', quantidade=40))

        grupos = self._painel()['por_pedido']

        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]['ordens'], 2)
        self.assertEqual(grupos[0]['receita'], Decimal('8000.00'))
        self.assertEqual(grupos[0]['pecas'], 100)

    def test_agrupa_por_produto_atravessando_pedidos(self):
        """
        É a leitura que interessa ao comercial: este produto dá margem, e
        não este pedido deu margem.
        """
        self._ficha(custo='20')
        a = self._pedido()
        b = self._pedido()
        self._terminar(self._ordem(a, preco='80', quantidade=60))
        self._terminar(self._ordem(b, preco='80', quantidade=40))

        grupos = self._painel()['por_produto']

        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]['nome'], 'Camisa')
        self.assertEqual(grupos[0]['pecas'], 100)
        self.assertEqual(grupos[0]['receita'], Decimal('8000.00'))

    def test_a_pior_margem_vem_primeiro(self):
        """
        A tela existe para achar o que está sendo vendido barato demais, e
        isso tem de estar na primeira linha.
        """
        boa = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM002', nome='Camisa boa',
        )
        self._ficha(custo='20')
        self._ficha(custo='20', produto=boa)
        pedido = self._pedido()
        self._terminar(self._ordem(pedido, preco='30', quantidade=100))
        self._terminar(self._ordem(pedido, preco='100', quantidade=100,
                                   produto=boa))

        nomes = [g['nome'] for g in self._painel()['por_produto']]

        self.assertEqual(nomes, ['Camisa', 'Camisa boa'])

    def test_o_sem_preco_vai_para_o_fim_da_lista(self):
        self._ficha(custo='20')
        pedido = self._pedido()
        amostra = self._pedido()
        self._terminar(self._ordem(pedido, preco='30', quantidade=100))
        self._terminar(self._ordem(amostra, preco='0', quantidade=10))

        grupos = self._painel()['por_pedido']

        self.assertFalse(grupos[0]['sem_preco'])
        self.assertTrue(grupos[-1]['sem_preco'])

    def test_a_margem_por_peca(self):
        self._ficha(custo='20')
        pedido = self._pedido()
        self._terminar(self._ordem(pedido, preco='80', quantidade=100))

        self.assertEqual(self._painel()['por_pedido'][0]['por_peca'],
                         Decimal('60.00'))

    def test_periodo_vazio_nao_estoura(self):
        painel = self._painel()

        self.assertEqual(painel['linhas'], [])
        self.assertEqual(painel['por_pedido'], [])
        self.assertEqual(painel['por_produto'], [])
        self.assertIsNone(painel['resumo']['margem_pct'])
        self.assertIsNone(painel['resumo']['pior'])


class TelaMargemTests(TestCase):
    """A tela renderizando de verdade, nas três visões."""

    @classmethod
    def setUpTestData(cls):
        from apps.core.models import PerfilAcesso, Usuario

        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Tela LTDA', nome_fantasia='Tela',
            cnpj='53345678000191', segmento='moda_confeccao',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Tela LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@teste.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def _dados(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Time', cpf_cnpj='12345678901',
        )
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa de jogo',
        )
        ficha = FichaTecnica.objects.create(filial=self.filial, produto=produto)
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha Dry', unidade=MaterialFicha.Unidade.METRO,
            consumo=Decimal('1'), custo_unitario=Decimal('20'),
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=cliente, numero=1,
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa',
            quantidade=100, valor_unitario=Decimal('80'),
        )
        ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            status=OrdemProducao.Status.CONCLUIDA,
            numero='OP-0001', ano=2026, sequencial=1, quantidade=100,
        )
        EtapaOrdem.objects.create(
            ordem=ordem, etapa=EtapaOrdem.Etapa.COSTURA, sequencia=6,
            status=EtapaOrdem.Status.CONCLUIDA, quantidade_produzida=100,
            data_conclusao=timezone.localdate(),
        )
        return produto, pedido

    def test_a_tela_abre_sem_dado_nenhum(self):
        resposta = self.client.get(reverse('moda:indicador-margens'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Nenhuma ordem concluída no período')

    def test_a_visao_por_pedido_e_o_padrao(self):
        self._dados()

        resposta = self.client.get(reverse('moda:indicador-margens'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Pedido 1')
        self.assertContains(resposta, '75,0%')

    def test_a_visao_por_produto(self):
        self._dados()

        resposta = self.client.get(
            reverse('moda:indicador-margens'), {'visao': 'produto'},
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Camisa de jogo')

    def test_a_visao_por_ordem(self):
        self._dados()

        resposta = self.client.get(
            reverse('moda:indicador-margens'), {'visao': 'ordem'},
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'OP-0001')

    def test_visao_invalida_cai_no_padrao_em_vez_de_estourar(self):
        self._dados()

        resposta = self.client.get(
            reverse('moda:indicador-margens'), {'visao': 'qualquer'},
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Pedido 1')

    def test_periodo_invalido_cai_no_padrao(self):
        resposta = self.client.get(
            reverse('moda:indicador-margens'), {'dias': 'trinta'},
        )

        self.assertEqual(resposta.status_code, 200)

    def test_a_rota_do_menu_cai_na_tela(self):
        from apps.moda.views_margem import MargemIndicadorView

        for url in (
            reverse('moda:indicador-margens'),
            reverse('moda:item', args=['indicadores', 'margens']),
        ):
            self.assertIs(resolve(url).func.view_class, MargemIndicadorView)
