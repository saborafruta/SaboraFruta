"""
Estoque de aviamentos — saldo contra a demanda das ordens abertas.

Aviamento não tem consumo apontado: ninguém registra quantos zíperes foram
pregados. Então a régua desta tela é a DEMANDA (o que as ordens abertas vão
consumir), e não um ritmo de gasto como na de tecidos. Os testes cercam:

  · o AGRUPAMENTO, que é a única regra aqui capaz de errar em silêncio —
    juntar demais esconde cadastro duplicado, juntar de menos inventa
    aviamento que não existe. E ele tem de bater com o do painel de
    necessidade, senão as duas telas discordam sobre o que falta comprar;
  · a fronteira entre "não sei" e "acabou": sem vínculo com estoque o saldo
    é traço, e um zero inventado vira alarme falso diário;
  · a reserva: descontar a reserva das PRÓPRIAS ordens faria reservar
    aumentar a falta, e quem reservasse pioraria o indicador.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import resolve, reverse

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.estoque.models.estoque import Estoque
from apps.moda.models import (
    FichaTecnica, ItemPedidoProducao, MaterialFicha, OrdemProducao,
    PedidoProducao, ProdutoModa, ReservaMaterial,
)
from apps.moda.services.estoque_aviamento import EstoqueAviamentoService
from apps.produtos.models import Produto, UnidadeMedida


class AviamentoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Aviamento LTDA', nome_fantasia='Aviamento',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Aviamento LTDA',
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

    def _produto_estoque(self, codigo='ZIP001', descricao='Ziper nº 5'):
        return Produto.objects.create(
            filial=self.filial, codigo=codigo, descricao=descricao,
            unidade_medida=self.unidade,
        )

    def _saldo(self, produto, atual='100', reservado='0', custo='3'):
        return Estoque.objects.create(
            produto=produto, filial=self.filial,
            quantidade_atual=Decimal(atual),
            quantidade_reservada=Decimal(reservado),
            quantidade_disponivel=Decimal(atual) - Decimal(reservado),
            custo_medio=Decimal(custo),
        )

    def _ficha(self, codigo='CAM001'):
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo=codigo, nome=f'Camisa {codigo}',
        )
        return FichaTecnica.objects.create(filial=self.filial, produto=produto)

    def _material(self, ficha, descricao='Ziper nº 5', codigo='',
                  tipo=MaterialFicha.Tipo.ZIPER, consumo='1', custo='3',
                  produto_estoque=None):
        return MaterialFicha.objects.create(
            ficha=ficha, tipo=tipo, descricao=descricao, codigo=codigo,
            unidade=MaterialFicha.Unidade.UNIDADE,
            consumo=Decimal(consumo), custo_unitario=Decimal(custo),
            produto_estoque=produto_estoque,
        )

    def _ordem(self, ficha, quantidade=100, sequencial=1,
               status=OrdemProducao.Status.EM_PRODUCAO):
        item = ItemPedidoProducao.objects.create(
            pedido=self.pedido, produto=ficha.produto,
            descricao='Camisa', quantidade=quantidade,
        )
        return OrdemProducao.objects.create(
            filial=self.filial, pedido=self.pedido, item=item, status=status,
            numero=f'OP-{sequencial:04d}', ano=2026, sequencial=sequencial,
            quantidade=quantidade,
        )

    def _painel(self, busca='', filtro=''):
        return EstoqueAviamentoService.painel(self.filial, busca, filtro)

    def _linha(self):
        return self._painel()['linhas'][0]


class AgrupamentoTests(AviamentoBase):
    """A única regra desta tela que pode errar em silêncio."""

    def test_o_mesmo_produto_de_estoque_em_duas_fichas_e_uma_linha_so(self):
        """
        Sem isso o comprador pediria o mesmo zíper duas vezes — e é a mesma
        chave que o painel de necessidade usa, de propósito.
        """
        produto = self._produto_estoque()
        self._saldo(produto)
        for codigo in ('CAM001', 'CAM002'):
            self._material(self._ficha(codigo), produto_estoque=produto)

        linhas = self._painel()['linhas']

        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]['fichas'], 2)

    def test_sem_vinculo_agrupa_pelo_codigo(self):
        self._material(self._ficha('CAM001'), codigo='Z5', descricao='Ziper n 5')
        self._material(self._ficha('CAM002'), codigo='Z5', descricao='Zíper nº 5')

        self.assertEqual(len(self._painel()['linhas']), 1)

    def test_descricoes_diferentes_sem_codigo_ficam_separadas(self):
        """
        Chato, e honesto: juntar por semelhança esconderia justamente o
        cadastro que precisa ser padronizado.
        """
        self._material(self._ficha('CAM001'), descricao='Ziper n5')
        self._material(self._ficha('CAM002'), descricao='Zíper nº 5')

        self.assertEqual(len(self._painel()['linhas']), 2)

    def test_a_chave_e_a_mesma_do_painel_de_necessidade(self):
        """
        Se as duas divergirem, a demanda não encontra a linha e a tela passa
        a dizer que não falta nada.
        """
        from apps.moda.services.necessidade import NecessidadeService

        produto = self._produto_estoque()
        self._saldo(produto, atual='0')
        ficha = self._ficha()
        self._material(ficha, produto_estoque=produto, consumo='2')
        self._ordem(ficha, quantidade=100)

        chaves_tela = {l['chave'] for l in self._painel()['linhas']}
        chaves_necessidade = {n.chave for n in NecessidadeService.calcular(self.filial)}

        self.assertTrue(chaves_tela)
        self.assertTrue(chaves_tela <= chaves_necessidade)

    def test_tecido_nao_e_aviamento(self):
        """Tecido e forro são o corpo da peça; o resto é o que se prega nela."""
        ficha = self._ficha()
        self._material(ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
                       descricao='Malha Dry')
        self._material(ficha, tipo=MaterialFicha.Tipo.ZIPER, descricao='Ziper')

        self.assertEqual(
            [l['descricao'] for l in self._painel()['linhas']], ['Ziper'],
        )


class SaldoTests(AviamentoBase):
    """A fronteira entre "não sei" e "acabou"."""

    def test_o_vinculo_traz_saldo_e_valor(self):
        produto = self._produto_estoque()
        self._saldo(produto, atual='100', reservado='20', custo='3')
        self._material(self._ficha(), produto_estoque=produto)

        linha = self._linha()

        self.assertTrue(linha['ligado'])
        self.assertEqual(linha['saldo'], Decimal('100'))
        self.assertEqual(linha['reservado'], Decimal('20'))
        self.assertEqual(linha['valor'], Decimal('300.00'))

    def test_sem_vinculo_o_saldo_e_traco_e_nao_zero(self):
        self._material(self._ficha())

        linha = self._linha()

        self.assertFalse(linha['ligado'])
        self.assertIsNone(linha['saldo'])
        self.assertIsNone(linha['livre'])
        self.assertIsNone(linha['falta'])
        self.assertIsNone(linha['valor'])

    def test_produto_ligado_sem_estoque_na_filial_e_zero_de_verdade(self):
        """
        Aqui o sistema SABE: o cadastro existe, a filial é que não tem o
        item. Diferente de não haver vínculo nenhum.
        """
        produto = self._produto_estoque()
        self._material(self._ficha(), produto_estoque=produto)

        linha = self._linha()

        self.assertTrue(linha['ligado'])
        self.assertEqual(linha['saldo'], Decimal('0'))
        self.assertEqual(linha['valor'], Decimal('0'))


class DemandaTests(AviamentoBase):
    """O que as ordens abertas pedem, contra o que há."""

    def test_a_demanda_e_o_consumo_da_ficha_vezes_a_ordem(self):
        """2 zíperes por peça em 100 peças são 200."""
        produto = self._produto_estoque()
        self._saldo(produto, atual='500')
        ficha = self._ficha()
        self._material(ficha, produto_estoque=produto, consumo='2')
        self._ordem(ficha, quantidade=100)

        linha = self._linha()

        self.assertEqual(linha['previsto'], Decimal('200'))
        self.assertEqual(linha['ordens'], 1)
        self.assertEqual(linha['falta'], Decimal('0'))

    def test_a_falta_e_o_que_o_estoque_nao_cobre(self):
        produto = self._produto_estoque()
        self._saldo(produto, atual='150')
        ficha = self._ficha()
        self._material(ficha, produto_estoque=produto, consumo='2')
        self._ordem(ficha, quantidade=100)

        self.assertEqual(self._linha()['falta'], Decimal('50.0000'))

    def test_ordem_encerrada_nao_pede_mais_nada(self):
        produto = self._produto_estoque()
        self._saldo(produto, atual='0')
        ficha = self._ficha()
        self._material(ficha, produto_estoque=produto, consumo='2')
        self._ordem(ficha, quantidade=100,
                    status=OrdemProducao.Status.CONCLUIDA)

        linha = self._linha()

        self.assertEqual(linha['previsto'], Decimal('0'))
        self.assertEqual(linha['falta'], Decimal('0'))

    def test_reservar_para_estas_ordens_nao_aumenta_a_falta(self):
        """
        `quantidade_disponivel` já desconta TODAS as reservas. Se a falta
        fosse medida contra ele, reservar — a ação que resolve o problema —
        pioraria o indicador, e o usuário reservaria até o estoque acabar.
        """
        produto = self._produto_estoque()
        self._saldo(produto, atual='200', reservado='200')
        ficha = self._ficha()
        material = self._material(ficha, produto_estoque=produto, consumo='2')
        ordem = self._ordem(ficha, quantidade=100)
        ReservaMaterial.objects.create(
            filial=self.filial, ordem=ordem, produto=produto,
            material=material, quantidade=Decimal('200'),
            status=ReservaMaterial.Status.ATIVA,
        )

        linha = self._linha()

        self.assertEqual(linha['livre'], Decimal('200'))
        self.assertEqual(linha['falta'], Decimal('0'))

    def test_reserva_de_outra_ordem_reduz_o_livre(self):
        """
        Material separado para outro trabalho não vai cobrir este — contá-lo
        como livre prometeria zíper que já tem dono.
        """
        produto = self._produto_estoque()
        self._saldo(produto, atual='200', reservado='150')
        ficha = self._ficha()
        self._material(ficha, produto_estoque=produto, consumo='2')
        self._ordem(ficha, quantidade=100)

        linha = self._linha()

        self.assertEqual(linha['livre'], Decimal('50'))
        self.assertEqual(linha['falta'], Decimal('150.0000'))


class OrdemEFiltroTests(AviamentoBase):

    def _cenario(self):
        falta_muito = self._produto_estoque(codigo='ZIP001', descricao='Ziper')
        falta_pouco = self._produto_estoque(codigo='BOT001', descricao='Botao')
        self._saldo(falta_muito, atual='0', custo='3')
        self._saldo(falta_pouco, atual='90', custo='1')
        ficha = self._ficha()
        self._material(ficha, descricao='Ziper', produto_estoque=falta_muito,
                       consumo='1')
        self._material(ficha, descricao='Botao', tipo=MaterialFicha.Tipo.BOTAO,
                       produto_estoque=falta_pouco, consumo='1')
        self._material(ficha, descricao='Etiqueta',
                       tipo=MaterialFicha.Tipo.ETIQUETA, consumo='1')
        self._ordem(ficha, quantidade=100)

    def test_o_que_falta_vem_primeiro_e_a_maior_falta_no_topo(self):
        """
        A tela responde "o que preciso comprar", e a resposta tem de estar
        na primeira linha — não no meio de uma lista alfabética.
        """
        self._cenario()

        self.assertEqual(
            [l['descricao'] for l in self._painel()['linhas']],
            ['Ziper', 'Botao', 'Etiqueta'],
        )

    def test_o_resumo_aponta_o_primeiro_a_parar(self):
        self._cenario()

        resumo = self._painel()['resumo']

        self.assertEqual(resumo['aviamentos'], 3)
        self.assertEqual(resumo['faltando'], 2)
        self.assertEqual(resumo['sem_vinculo'], 1)
        self.assertEqual(resumo['pior']['descricao'], 'Ziper')
        # 0 × 3 + 90 × 1; a etiqueta sem vínculo não entra no valor.
        self.assertEqual(resumo['valor'], Decimal('90.00'))

    def test_o_resumo_conta_a_lista_inteira_e_nao_a_filtrada(self):
        """
        Filtrar por "faltando" não pode zerar o contador de "sem estoque" —
        o cabeçalho descreve a fábrica, não o recorte da tela.
        """
        self._cenario()

        painel = self._painel(filtro='faltando')

        self.assertEqual(len(painel['linhas']), 2)
        self.assertEqual(painel['resumo']['aviamentos'], 3)
        self.assertEqual(painel['resumo']['sem_vinculo'], 1)

    def test_o_filtro_sem_vinculo(self):
        self._cenario()

        self.assertEqual(
            [l['descricao'] for l in self._painel(filtro='sem_vinculo')['linhas']],
            ['Etiqueta'],
        )

    def test_a_busca_filtra_por_descricao_e_codigo(self):
        self._cenario()

        self.assertEqual(
            [l['descricao'] for l in self._painel(busca='etiq')['linhas']],
            ['Etiqueta'],
        )
        self.assertEqual(
            [l['descricao'] for l in self._painel(busca='ZIP001')['linhas']], [],
        )

    def test_preco_divergente_e_marcado(self):
        """
        O mesmo zíper com dois valores significa que um dos custos está
        velho — e o custo da peça sai errado dos dois jeitos.
        """
        produto = self._produto_estoque()
        self._saldo(produto)
        self._material(self._ficha('CAM001'), produto_estoque=produto, custo='3')
        self._material(self._ficha('CAM002'), produto_estoque=produto, custo='4')

        linha = self._linha()

        self.assertTrue(linha['preco_divergente'])
        self.assertEqual(self._painel()['resumo']['preco_divergente'], 1)

    def test_sem_aviamento_nenhum_nao_estoura(self):
        painel = self._painel()

        self.assertEqual(painel['linhas'], [])
        self.assertIsNone(painel['resumo']['pior'])
        self.assertEqual(painel['resumo']['valor'], Decimal('0.00'))


class TelaEstoqueAviamentoTests(TestCase):
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
        resposta = self.client.get(reverse('moda:estoque-aviamentos'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Nenhum aviamento nas fichas')

    def test_a_tela_abre_com_falta(self):
        unidade = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        produto_estoque = Produto.objects.create(
            filial=self.filial, codigo='ZIP001', descricao='Ziper nº 5',
            unidade_medida=unidade,
        )
        Estoque.objects.create(
            produto=produto_estoque, filial=self.filial,
            quantidade_atual=Decimal('50'), quantidade_reservada=Decimal('0'),
            quantidade_disponivel=Decimal('50'), custo_medio=Decimal('3'),
        )
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Time', cpf_cnpj='12345678901',
        )
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        ficha = FichaTecnica.objects.create(filial=self.filial, produto=produto)
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.ZIPER, descricao='Ziper nº 5',
            unidade=MaterialFicha.Unidade.UNIDADE, consumo=Decimal('1'),
            custo_unitario=Decimal('3'), produto_estoque=produto_estoque,
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=cliente, numero=1,
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa', quantidade=100,
        )
        OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            status=OrdemProducao.Status.EM_PRODUCAO,
            numero='OP-0001', ano=2026, sequencial=1, quantidade=100,
        )

        resposta = self.client.get(reverse('moda:estoque-aviamentos'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Ziper nº 5')
        self.assertContains(resposta, '50,00')

    def test_filtro_invalido_e_ignorado_em_vez_de_estourar(self):
        resposta = self.client.get(
            reverse('moda:estoque-aviamentos'), {'filtro': 'qualquer'},
        )

        self.assertEqual(resposta.status_code, 200)

    def test_a_rota_do_menu_cai_na_tela(self):
        from apps.moda.views_estoque_aviamento import EstoqueAviamentoView

        for url in (
            reverse('moda:estoque-aviamentos'),
            reverse('moda:item', args=['estoque', 'aviamentos']),
        ):
            self.assertIs(resolve(url).func.view_class, EstoqueAviamentoView)

    def test_a_tela_de_engenharia_continua_sendo_outra(self):
        """
        `engenharia/aviamentos/` responde "quais fichas usam este zíper";
        esta responde "quanto tem e as ordens cabem nisso".
        """
        from apps.moda.views_insumos import AviamentoListView

        self.assertIs(
            resolve(reverse('moda:aviamentos')).func.view_class, AviamentoListView,
        )
