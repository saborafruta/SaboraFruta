"""
Estoque de produtos — saldo, o que está vindo e o que está parado.

O SALDO SOZINHO ENGANA NUMA CONFECÇÃO, e é essa a premissa que os testes
protegem. A maior parte do que sai é feito sob encomenda: a peça nasce
contra um pedido e vai embora sem nunca virar estoque. Um produto zerado com
duzentas peças na linha está bem; um com trinta paradas há oito meses está
mal — e o número que os separa não é o saldo.

Os jeitos de a tela mentir:

  · contar como "parado" um produto que nunca teve peça — zerado há um ano
    não é dinheiro preso, é produto que não se faz mais;
  · mostrar zero onde não há vínculo com o ERP, quando o certo é traço:
    numa peça feita só sob encomenda a ausência de saldo é o normal;
  · esquecer o que está em produção, e aí um saldo baixo parece problema;
  · deixar o cabeçalho descrever o recorte da tela em vez da fábrica.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.estoque.models.estoque import Estoque
from apps.moda.models import (
    Cor, ItemPedidoProducao, OrdemProducao, PedidoProducao, ProdutoCor,
    ProdutoModa, Tamanho, Variante,
)
from apps.moda.services.estoque_produto import DIAS_PARADO, EstoqueProdutoService
from apps.produtos.models import Produto, UnidadeMedida


class ProdutoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Produto LTDA', nome_fantasia='Produto',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Produto LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Time', cpf_cnpj='12345678901',
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )

    def setUp(self):
        self.pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=1,
        )

    # ── Montagem ─────────────────────────────────────────────────────────

    def _produto(self, codigo='CAM001', nome='Camisa', erp=None,
                 status=ProdutoModa.Status.ATIVO):
        return ProdutoModa.objects.create(
            filial=self.filial, codigo=codigo, nome=nome,
            produto_erp=erp, status=status,
        )

    def _erp(self, codigo='ERP001', descricao='Camisa'):
        return Produto.objects.create(
            filial=self.filial, codigo=codigo, descricao=descricao,
            unidade_medida=self.unidade,
        )

    def _saldo(self, erp, atual='30', reservado='0', custo='40',
               saida_dias_atras=None, entrada_dias_atras=None):
        agora = timezone.now()
        return Estoque.objects.create(
            produto=erp, filial=self.filial,
            quantidade_atual=Decimal(atual),
            quantidade_reservada=Decimal(reservado),
            quantidade_disponivel=Decimal(atual) - Decimal(reservado),
            custo_medio=Decimal(custo),
            ultima_saida=(
                None if saida_dias_atras is None
                else agora - timedelta(days=saida_dias_atras)
            ),
            ultima_entrada=(
                None if entrada_dias_atras is None
                else agora - timedelta(days=entrada_dias_atras)
            ),
        )

    def _ordem(self, produto, quantidade=200, sequencial=1,
               status=OrdemProducao.Status.EM_PRODUCAO):
        item = ItemPedidoProducao.objects.create(
            pedido=self.pedido, produto=produto,
            descricao='Camisa', quantidade=quantidade,
        )
        return OrdemProducao.objects.create(
            filial=self.filial, pedido=self.pedido, item=item, status=status,
            numero=f'OP-{sequencial:04d}', ano=2026, sequencial=sequencial,
            quantidade=quantidade,
        )

    def _painel(self, busca='', filtro=''):
        return EstoqueProdutoService.painel(self.filial, busca, filtro)

    def _linha(self):
        return self._painel()['linhas'][0]


class SaldoTests(ProdutoBase):
    """A fronteira entre "não sei", "não guarda" e "acabou"."""

    def test_o_vinculo_com_o_erp_traz_saldo_e_valor(self):
        erp = self._erp()
        self._saldo(erp, atual='30', reservado='5', custo='40')
        self._produto(erp=erp)

        linha = self._linha()

        self.assertTrue(linha['ligado'])
        self.assertEqual(linha['saldo'], Decimal('30'))
        self.assertEqual(linha['reservado'], Decimal('5'))
        self.assertEqual(linha['disponivel'], Decimal('25'))
        self.assertEqual(linha['valor'], Decimal('1200.00'))

    def test_sem_vinculo_o_saldo_e_traco_e_nao_zero(self):
        """
        Numa peça feita só sob encomenda a ausência de saldo é o normal.
        Zero diria "acabou"; traço diz "não guarda estoque disso".
        """
        self._produto()

        linha = self._linha()

        self.assertFalse(linha['ligado'])
        self.assertIsNone(linha['saldo'])
        self.assertIsNone(linha['valor'])
        self.assertIsNone(linha['dias_parado'])

    def test_ligado_sem_estoque_na_filial_e_zero_de_verdade(self):
        """O cadastro existe; a filial é que não tem a peça."""
        erp = self._erp()
        self._produto(erp=erp)

        linha = self._linha()

        self.assertTrue(linha['ligado'])
        self.assertEqual(linha['saldo'], Decimal('0'))
        self.assertEqual(linha['valor'], Decimal('0'))

    def test_o_saldo_e_da_filial_ativa(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Filial 2',
            cnpj='53345678000353', uf='RN', cidade='Mossoro',
        )
        erp = self._erp()
        Estoque.objects.create(
            produto=erp, filial=outra, quantidade_atual=Decimal('999'),
            quantidade_disponivel=Decimal('999'), custo_medio=Decimal('40'),
        )
        self._produto(erp=erp)

        self.assertEqual(self._linha()['saldo'], Decimal('0'))

    def test_conta_os_skus_ativos(self):
        """
        O saldo é do PRODUTO, não do SKU: a variante tem código de barras
        mas não tem estoque próprio. A contagem fica na linha para essa
        limitação não passar despercebida.
        """
        produto = self._produto()
        cor = Cor.objects.create(filial=self.filial, nome='Amarelo')
        produto_cor = ProdutoCor.objects.create(produto=produto, cor=cor)
        for i, sigla in enumerate(('M', 'G')):
            tamanho = Tamanho.objects.create(
                filial=self.filial, sigla=sigla, ordem=(i + 1) * 10,
            )
            Variante.objects.create(
                produto=produto, produto_cor=produto_cor, tamanho=tamanho,
                sku=f'CAM001-AM-{sigla}',
            )

        self.assertEqual(self._linha()['skus'], 2)


class ParadoTests(ProdutoBase):
    """Peça pronta que sobrou de um pedido e nunca saiu."""

    def test_conta_os_dias_desde_a_ultima_saida(self):
        erp = self._erp()
        self._saldo(erp, atual='30', saida_dias_atras=120)
        self._produto(erp=erp)

        linha = self._linha()

        self.assertEqual(linha['dias_parado'], 120)
        self.assertGreaterEqual(linha['dias_parado'], DIAS_PARADO)

    def test_sem_saida_nenhuma_conta_desde_a_entrada(self):
        """
        Peça que entrou e nunca saiu é o caso mais preso de todos — deixá-la
        sem tempo parado a esconderia justamente do filtro que a procura.
        """
        erp = self._erp()
        self._saldo(erp, atual='30', saida_dias_atras=None, entrada_dias_atras=200)
        self._produto(erp=erp)

        self.assertEqual(self._linha()['dias_parado'], 200)

    def test_produto_zerado_nao_e_dinheiro_preso(self):
        """
        Zerado há um ano não é estoque parado, é produto que não se faz
        mais. Contá-lo encheria o filtro de linhas sem dinheiro nenhum.
        """
        erp = self._erp()
        self._saldo(erp, atual='0', saida_dias_atras=365)
        self._produto(erp=erp)

        linha = self._linha()

        self.assertIsNone(linha['dias_parado'])
        self.assertEqual(self._painel()['resumo']['parados'], 0)

    def test_saldo_novo_nao_entra_no_parado(self):
        erp = self._erp()
        self._saldo(erp, atual='30', saida_dias_atras=10)
        self._produto(erp=erp)

        painel = self._painel()

        self.assertEqual(painel['linhas'][0]['dias_parado'], 10)
        self.assertEqual(painel['resumo']['parados'], 0)

    def test_o_pior_e_o_de_maior_valor_e_nao_o_de_mais_tempo(self):
        """
        Seis meses de uma peça de dez reais não é o problema que trinta
        dias de mil reais é.
        """
        antigo = self._erp(codigo='ERP001', descricao='Bone')
        caro = self._erp(codigo='ERP002', descricao='Jaqueta')
        self._saldo(antigo, atual='10', custo='10', saida_dias_atras=300)
        self._saldo(caro, atual='20', custo='200', saida_dias_atras=100)
        self._produto(codigo='BON001', nome='Bone', erp=antigo)
        self._produto(codigo='JAQ001', nome='Jaqueta', erp=caro)

        resumo = self._painel()['resumo']

        self.assertEqual(resumo['parados'], 2)
        self.assertEqual(resumo['pior']['nome'], 'Jaqueta')
        self.assertEqual(resumo['valor_parado'], Decimal('4100.00'))


class ProducaoTests(ProdutoBase):
    """O que está vindo."""

    def test_soma_as_pecas_das_ordens_abertas(self):
        """
        Sem esta coluna um saldo baixo parece problema quando há duzentas
        peças na costura.
        """
        produto = self._produto()
        self._ordem(produto, quantidade=200, sequencial=1)
        self._ordem(produto, quantidade=50, sequencial=2)

        linha = self._linha()

        self.assertEqual(linha['em_producao'], 250)
        self.assertEqual(linha['ordens'], 2)

    def test_ordem_encerrada_nao_esta_mais_vindo(self):
        produto = self._produto()
        self._ordem(produto, quantidade=200,
                    status=OrdemProducao.Status.CONCLUIDA)

        self.assertEqual(self._linha()['em_producao'], 0)

    def test_ordem_cancelada_nao_esta_mais_vindo(self):
        produto = self._produto()
        self._ordem(produto, quantidade=200,
                    status=OrdemProducao.Status.CANCELADA)

        self.assertEqual(self._linha()['em_producao'], 0)

    def test_produto_zerado_com_producao_em_curso_nao_e_problema(self):
        """A premissa da tela: saldo zero e duzentas na linha está bem."""
        erp = self._erp()
        self._saldo(erp, atual='0')
        produto = self._produto(erp=erp)
        self._ordem(produto, quantidade=200)

        linha = self._linha()

        self.assertEqual(linha['saldo'], Decimal('0'))
        self.assertEqual(linha['em_producao'], 200)
        self.assertIsNone(linha['dias_parado'])


class OrdemEFiltroTests(ProdutoBase):

    def _cenario(self):
        caro = self._erp(codigo='ERP001', descricao='Jaqueta')
        barato = self._erp(codigo='ERP002', descricao='Bone')
        self._saldo(caro, atual='20', custo='200', saida_dias_atras=120)
        self._saldo(barato, atual='10', custo='10', saida_dias_atras=5)
        self._produto(codigo='JAQ001', nome='Jaqueta', erp=caro)
        self._produto(codigo='BON001', nome='Bone', erp=barato)
        sob_encomenda = self._produto(codigo='CAM001', nome='Camisa de time')
        self._ordem(sob_encomenda, quantidade=200)

    def test_ordena_pelo_maior_valor_parado(self):
        """
        A pergunta é "onde meu dinheiro está preso", e a resposta tem de
        estar na primeira linha — não no meio de uma lista alfabética.
        """
        self._cenario()

        self.assertEqual(
            [l['nome'] for l in self._painel()['linhas']],
            ['Jaqueta', 'Bone', 'Camisa de time'],
        )

    def test_o_resumo_conta_a_fabrica_e_nao_o_recorte(self):
        """
        Filtrar por "parado" não pode zerar o contador de "sem vínculo": o
        cabeçalho descreve a fábrica, não a tela.
        """
        self._cenario()

        painel = self._painel(filtro='parado')

        self.assertEqual([l['nome'] for l in painel['linhas']], ['Jaqueta'])
        self.assertEqual(painel['resumo']['produtos'], 3)
        self.assertEqual(painel['resumo']['sem_vinculo'], 1)
        self.assertEqual(painel['resumo']['com_saldo'], 2)
        self.assertEqual(painel['resumo']['em_producao'], 200)

    def test_o_filtro_de_producao(self):
        self._cenario()

        self.assertEqual(
            [l['nome'] for l in self._painel(filtro='em_producao')['linhas']],
            ['Camisa de time'],
        )

    def test_o_filtro_sem_vinculo(self):
        self._cenario()

        self.assertEqual(
            [l['nome'] for l in self._painel(filtro='sem_vinculo')['linhas']],
            ['Camisa de time'],
        )

    def test_a_busca_filtra_por_nome_codigo_e_referencia(self):
        self._cenario()

        self.assertEqual(
            [l['nome'] for l in self._painel(busca='jaq')['linhas']], ['Jaqueta'],
        )
        self.assertEqual(
            [l['nome'] for l in self._painel(busca='BON001')['linhas']], ['Bone'],
        )

    def test_produto_inativo_nao_aparece(self):
        produto = self._produto()
        produto.ativo = False
        produto.save(update_fields=['ativo'])

        self.assertEqual(self._painel()['linhas'], [])

    def test_descontinuado_continua_aparecendo_e_e_marcado(self):
        """
        Produto descontinuado com saldo é justamente o que interessa: é
        peça que não se faz mais e continua ocupando dinheiro.
        """
        erp = self._erp()
        self._saldo(erp, atual='30', saida_dias_atras=200)
        self._produto(erp=erp, status=ProdutoModa.Status.DESCONTINUADO)

        linha = self._linha()

        self.assertTrue(linha['descontinuado'])
        self.assertEqual(linha['dias_parado'], 200)

    def test_sem_produto_nenhum_nao_estoura(self):
        painel = self._painel()

        self.assertEqual(painel['linhas'], [])
        self.assertIsNone(painel['resumo']['pior'])
        self.assertEqual(painel['resumo']['valor'], Decimal('0.00'))


class TelaEstoqueProdutoTests(TestCase):
    """A tela renderizando de verdade."""

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

    def test_a_tela_abre_sem_dado_nenhum(self):
        resposta = self.client.get(reverse('moda:estoque-produtos'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Nenhum produto ativo no catálogo')

    def test_a_tela_abre_com_produto_parado(self):
        unidade = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        erp = Produto.objects.create(
            filial=self.filial, codigo='ERP001', descricao='Jaqueta',
            unidade_medida=unidade,
        )
        Estoque.objects.create(
            produto=erp, filial=self.filial,
            quantidade_atual=Decimal('20'), quantidade_reservada=Decimal('0'),
            quantidade_disponivel=Decimal('20'), custo_medio=Decimal('200'),
            ultima_saida=timezone.now() - timedelta(days=120),
        )
        ProdutoModa.objects.create(
            filial=self.filial, codigo='JAQ001', nome='Jaqueta', produto_erp=erp,
        )

        resposta = self.client.get(reverse('moda:estoque-produtos'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Jaqueta')
        # Sem separador de milhar: o ERP inteiro renderiza assim, e mudar
        # so' nesta tela a deixaria diferente das outras quatro do grupo.
        self.assertContains(resposta, '4000,00')
        self.assertContains(resposta, '120 dias')

    def test_a_tela_avisa_o_que_nao_guarda_estoque(self):
        ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa de time',
        )

        resposta = self.client.get(reverse('moda:estoque-produtos'))

        self.assertContains(resposta, 'sem ligação com o catálogo do ERP')
        self.assertContains(resposta, 'feito sob encomenda')

    def test_filtro_invalido_e_ignorado_em_vez_de_estourar(self):
        resposta = self.client.get(
            reverse('moda:estoque-produtos'), {'filtro': 'qualquer'},
        )

        self.assertEqual(resposta.status_code, 200)

    def test_a_rota_do_menu_cai_na_tela(self):
        from apps.moda.views_estoque_produto import EstoqueProdutoView

        for url in (
            reverse('moda:estoque-produtos'),
            reverse('moda:item', args=['estoque', 'produtos']),
        ):
            self.assertIs(resolve(url).func.view_class, EstoqueProdutoView)

    def test_o_catalogo_continua_sendo_outra_tela(self):
        """
        `produtos/produtos` responde "quais produtos existem e como são";
        esta responde "quanto há de cada um".
        """
        catalogo = reverse('moda:item', args=['produtos', 'produtos'])
        estoque = reverse('moda:estoque-produtos')

        self.assertNotEqual(catalogo, estoque)
        self.assertEqual(self.client.get(catalogo).status_code, 200)
