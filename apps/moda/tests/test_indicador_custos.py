"""
Indicador de custos — real contra a ficha técnica.

Custo é a tela em que o erro sai mais caro: ninguém confere um número em
reais que parece razoável, e quem fecha preço em cima dele fecha com
prejuízo e só descobre no fim do mês. Os testes aqui cercam os quatro jeitos
de o número sair plausível e errado:

  · ordem no meio da produção — real parcial contra previsto inteiro
    apareceria como uma economia enorme que não existe;
  · o não medido contado como zero — aviamento sem apontamento derrubaria o
    custo de toda ordem;
  · operação de setor que o fluxo não acompanha ficando só no previsto,
    virando economia que ninguém produziu;
  · custo por peça dividido pela quantidade emitida em vez das peças boas,
    o que apaga justamente o efeito do refugo no custo unitário.

Os valores são redondos de propósito: 1 m a R$ 20, 10 min a R$ 60/h = R$ 10.
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
    Roteiro, Tecido,
)
from apps.moda.services.custo_real import CustoRealService, preco_do_metro


class CustoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Custo LTDA', nome_fantasia='Custo',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Custo LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Time', cpf_cnpj='12345678901',
        )

    def setUp(self):
        self.produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        self.pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=1,
        )

    # ── Montagem ─────────────────────────────────────────────────────────

    def _ficha(self, tecido_consumo='1', tecido_preco='20', aviamento=None,
               produto=None):
        ficha = FichaTecnica.objects.create(
            filial=self.filial, produto=produto or self.produto,
        )
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha Dry', unidade=MaterialFicha.Unidade.METRO,
            consumo=Decimal(tecido_consumo), custo_unitario=Decimal(tecido_preco),
        )
        if aviamento:
            MaterialFicha.objects.create(
                ficha=ficha, tipo=MaterialFicha.Tipo.AVIAMENTO,
                descricao='Zíper', unidade=MaterialFicha.Unidade.UNIDADE,
                consumo=Decimal('1'), custo_unitario=Decimal(aviamento),
            )
        return ficha

    def _roteiro(self, operacoes, produto=None):
        """`operacoes` = lista de (setor, minutos, tipo_custo, custo)."""
        produto = produto or self.produto
        roteiro = Roteiro.objects.create(filial=self.filial, produto=produto)
        for i, (setor, minutos, tipo, custo) in enumerate(operacoes, start=1):
            operacao = Operacao.objects.create(
                filial=self.filial, nome=f'{setor}-{produto.codigo}-{i}',
                setor=setor, tempo_padrao=Decimal(minutos),
                tipo_custo=tipo, custo=Decimal(custo),
            )
            OperacaoRoteiro.objects.create(
                roteiro=roteiro, operacao=operacao, sequencia=i * 10,
            )
        return roteiro

    def _ordem(self, quantidade=10, sequencial=1, produto=None,
               status=OrdemProducao.Status.CONCLUIDA):
        item = ItemPedidoProducao.objects.create(
            pedido=self.pedido, produto=produto or self.produto,
            descricao='Camisa', quantidade=quantidade,
        )
        return OrdemProducao.objects.create(
            filial=self.filial, pedido=self.pedido, item=item, status=status,
            numero=f'OP-{sequencial:04d}', ano=2026, sequencial=sequencial,
            quantidade=quantidade,
        )

    def _etapa(self, ordem, etapa, sequencia, produzido, minutos=None, dias_atras=1):
        return EtapaOrdem.objects.create(
            ordem=ordem, etapa=etapa, sequencia=sequencia,
            status=EtapaOrdem.Status.CONCLUIDA,
            quantidade_produzida=produzido, tempo_minutos=minutos,
            data_conclusao=timezone.localdate() - timedelta(days=dias_atras),
        )

    def _corte(self, ordem, consumo, numero=1, quantidade=10,
               status=RegistroCorte.Status.CORTADO):
        return RegistroCorte.objects.create(
            filial=self.filial, ordem=ordem, numero=numero, status=status,
            quantidade=quantidade, data=timezone.localdate(),
            consumo_real=Decimal(consumo),
        )

    def _painel(self, dias=30):
        return CustoRealService.painel(self.filial, dias)

    def _linha(self, dias=30):
        return self._painel(dias)['linhas'][0]


class RecorteTests(CustoBase):
    """Quem entra na conta — e por quê."""

    def test_ordem_no_meio_da_producao_nao_entra(self):
        """
        O jeito mais fácil de esta tela mentir: real parcial contra previsto
        inteiro apareceria como uma economia enorme que não existe.
        """
        self._ficha()
        ordem = self._ordem(status=OrdemProducao.Status.EM_PRODUCAO)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, 4, produzido=10)

        self.assertEqual(self._painel()['linhas'], [])

    def test_ordem_cancelada_nao_entra(self):
        self._ficha()
        ordem = self._ordem(status=OrdemProducao.Status.CANCELADA)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, 4, produzido=10)

        self.assertEqual(self._painel()['linhas'], [])

    def test_a_janela_e_a_da_ultima_etapa_concluida(self):
        """A ordem não tem data de conclusão própria; o fluxo tem."""
        self._ficha()
        ordem = self._ordem()
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, 4, produzido=10, dias_atras=60)

        self.assertEqual(self._painel(dias=30)['linhas'], [])
        self.assertEqual(len(self._painel(dias=90)['linhas']), 1)

    def test_ordenado_pela_maior_variacao_em_reais(self):
        """
        A pergunta é onde o dinheiro sumiu: 5% de uma ordem grande pesa mais
        que 50% de uma pequena.
        """
        self._ficha()
        pequena = self._ordem(quantidade=10, sequencial=1)
        grande = self._ordem(quantidade=100, sequencial=2)
        self._etapa(pequena, EtapaOrdem.Etapa.CORTE, 4, produzido=10)
        self._etapa(grande, EtapaOrdem.Etapa.CORTE, 4, produzido=100)
        # Pequena: previsto 200, gastou 15 m -> 300. Estouro de 100.
        self._corte(pequena, consumo=15, numero=1)
        # Grande: previsto 2000, gastou 110 m -> 2200. Estouro de 200.
        self._corte(grande, consumo=110, numero=2)

        linhas = self._painel()['linhas']

        self.assertEqual([l['numero'] for l in linhas], ['OP-0002', 'OP-0001'])
        self.assertEqual(linhas[0]['variacao'], Decimal('200.00'))


class MaterialTests(CustoBase):
    """Tecido medido, aviamento estimado."""

    def test_o_tecido_real_sai_dos_metros_da_mesa(self):
        """10 peças a 1 m e R$ 20 preveem R$ 200; gastou 12 m = R$ 240."""
        self._ficha(tecido_consumo='1', tecido_preco='20')
        ordem = self._ordem(quantidade=10)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, 4, produzido=10)
        self._corte(ordem, consumo=12)

        linha = self._linha()

        self.assertEqual(linha['material']['tecido_previsto'], Decimal('200.00'))
        self.assertEqual(linha['material']['tecido_real'], Decimal('240.00'))
        self.assertEqual(linha['material']['tecido_variacao'], Decimal('40.00'))
        self.assertTrue(linha['material']['medido'])

    def test_o_aviamento_entra_pelo_previsto_e_a_linha_declara(self):
        """
        Não existe apontamento de quantos zíperes foram usados. Contá-los
        como zero deixaria toda ordem barata; carregá-los pelo previsto é o
        que sobra — desde que a tela diga.
        """
        self._ficha(tecido_preco='20', aviamento='3')
        ordem = self._ordem(quantidade=10)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, 4, produzido=10)
        self._corte(ordem, consumo=10)

        linha = self._linha()

        # Tecido bateu com o previsto; o zíper (R$ 30) veio do plano.
        self.assertEqual(linha['material']['outros'], Decimal('30.00'))
        self.assertEqual(linha['material']['real'], Decimal('230.00'))
        self.assertTrue(linha['estimado'])

    def test_sem_corte_medido_o_tecido_vai_pelo_previsto_e_e_declarado(self):
        self._ficha(tecido_preco='20')
        ordem = self._ordem(quantidade=10)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, 4, produzido=10)

        linha = self._linha()

        self.assertFalse(linha['material']['medido'])
        self.assertEqual(linha['material']['real'], Decimal('200.00'))
        self.assertEqual(linha['material']['tecido_variacao'], Decimal('0.00'))
        self.assertTrue(linha['estimado'])

    def test_corte_apenas_planejado_nao_conta_como_medida(self):
        """Tecido só virou custo depois de passar na mesa."""
        self._ficha(tecido_preco='20')
        ordem = self._ordem(quantidade=10)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, 4, produzido=10)
        self._corte(ordem, consumo=50, status=RegistroCorte.Status.PLANEJADO)

        self.assertFalse(self._linha()['material']['medido'])

    def test_o_preco_do_metro_e_ponderado_pelo_consumo(self):
        """
        Média simples de um forro caro usado em 10 cm com a malha barata
        usada em 1,20 m daria um preço que não existe.
        """
        ficha = self._ficha(tecido_consumo='1.20', tecido_preco='10')
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Forro', unidade=MaterialFicha.Unidade.METRO,
            consumo=Decimal('0.10'), custo_unitario=Decimal('100'),
        )

        # (1,20 × 10 + 0,10 × 100) / 1,30 = 22 / 1,30 = 16,9231
        self.assertEqual(preco_do_metro(ficha), Decimal('16.9231'))

    def test_ordem_sem_ficha_e_declarada(self):
        ordem = self._ordem(quantidade=10)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, 4, produzido=10)

        linha = self._linha()

        self.assertTrue(linha['sem_ficha'])
        self.assertEqual(linha['material']['previsto'], Decimal('0.00'))


class MaoDeObraTests(CustoBase):
    """Hora e peça se pagam de maneiras diferentes."""

    def test_pago_por_hora_o_tempo_apontado_manda(self):
        """
        10 min/peça a R$ 60/h preveem R$ 10 por peça — R$ 100 nas dez.
        Levou 150 min em vez de 100: R$ 150.
        """
        self._ficha(tecido_preco='0')
        self._roteiro([(Operacao.Setor.COSTURA, 10, Operacao.TipoCusto.POR_HORA, 60)])
        ordem = self._ordem(quantidade=10)
        self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, 6, produzido=10,
                    minutos=Decimal('150'))

        linha = self._linha()

        self.assertEqual(linha['obra']['previsto'], Decimal('100.00'))
        self.assertEqual(linha['obra']['real'], Decimal('150.00'))

    def test_pago_por_peca_demorar_o_dobro_custa_o_mesmo(self):
        """
        Facção não cobra pelo relógio. Aplicar a lógica da hora aqui
        inventaria um estouro que ninguém vai pagar.
        """
        self._ficha(tecido_preco='0')
        self._roteiro([(Operacao.Setor.COSTURA, 10, Operacao.TipoCusto.POR_PECA, 8)])
        ordem = self._ordem(quantidade=10)
        self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, 6, produzido=10,
                    minutos=Decimal('999'))

        linha = self._linha()

        self.assertEqual(linha['obra']['previsto'], Decimal('80.00'))
        self.assertEqual(linha['obra']['real'], Decimal('80.00'))

    def test_sem_tempo_apontado_a_hora_vai_pelo_padrao_e_e_declarada(self):
        self._ficha(tecido_preco='0')
        self._roteiro([(Operacao.Setor.COSTURA, 10, Operacao.TipoCusto.POR_HORA, 60)])
        ordem = self._ordem(quantidade=10)
        self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, 6, produzido=10)

        linha = self._linha()

        self.assertEqual(linha['obra']['real'], Decimal('100.00'))
        self.assertTrue(linha['obra']['estimado'])

    def test_setor_fora_do_fluxo_fica_fora_dos_dois_lados(self):
        """
        Modelagem não tem etapa que a acompanhe. Deixá-la só no previsto
        criaria uma economia permanente que nada no chão produziu.
        """
        self._ficha(tecido_preco='0')
        self._roteiro([
            (Operacao.Setor.MODELAGEM, 30, Operacao.TipoCusto.POR_HORA, 60),
            (Operacao.Setor.COSTURA, 10, Operacao.TipoCusto.POR_HORA, 60),
        ])
        ordem = self._ordem(quantidade=10)
        self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, 6, produzido=10,
                    minutos=Decimal('100'))

        linha = self._linha()

        self.assertEqual(linha['obra']['previsto'], Decimal('100.00'))
        self.assertEqual(linha['obra']['real'], Decimal('100.00'))
        self.assertEqual(linha['obra']['fora_do_fluxo'], Decimal('300.00'))
        self.assertEqual(linha['variacao'], Decimal('0.00'))

    def test_a_faccao_paga_pelas_pecas_que_sairam(self):
        """Peça refugada na bancada não é peça entregue pela facção."""
        self._ficha(tecido_preco='0')
        self._roteiro([(Operacao.Setor.COSTURA, 10, Operacao.TipoCusto.POR_PECA, 8)])
        ordem = self._ordem(quantidade=10)
        self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, 6, produzido=8)

        self.assertEqual(self._linha()['obra']['real'], Decimal('64.00'))

    def test_ordem_sem_roteiro_e_declarada(self):
        self._ficha(tecido_preco='20')
        ordem = self._ordem(quantidade=10)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, 4, produzido=10)

        linha = self._linha()

        self.assertTrue(linha['sem_roteiro'])
        self.assertEqual(linha['obra']['previsto'], Decimal('0.00'))


class CustoUnitarioTests(CustoBase):
    """O total e o por peça são perguntas diferentes."""

    def test_o_custo_por_peca_divide_pelas_boas(self):
        """
        Refugo não vira mercadoria, mas o dinheiro dele já foi gasto.
        Dividir pela quantidade emitida apagaria exatamente esse efeito.
        """
        self._ficha(tecido_consumo='1', tecido_preco='20')
        ordem = self._ordem(quantidade=10)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, 4, produzido=10)
        # A costura entregou 8: duas morreram depois de já terem custado.
        self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, 6, produzido=8)
        self._corte(ordem, consumo=10)

        linha = self._linha()

        self.assertEqual(linha['boas'], 8)
        self.assertEqual(linha['unitario_previsto'], Decimal('20.00'))
        # R$ 200 gastos para entregar 8 peças = R$ 25 cada.
        self.assertEqual(linha['unitario_real'], Decimal('25.00'))

    def test_uma_ordem_pode_fechar_no_total_e_estourar_por_peca(self):
        """
        É o caso que justifica as duas colunas: o orçamento fechou e mesmo
        assim a peça saiu cara, porque saíram menos peças.
        """
        self._ficha(tecido_consumo='1', tecido_preco='20')
        ordem = self._ordem(quantidade=10)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, 4, produzido=10)
        self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, 6, produzido=5)
        self._corte(ordem, consumo=10)

        linha = self._linha()

        self.assertEqual(linha['variacao'], Decimal('0.00'))
        self.assertFalse(linha['estourou'])
        self.assertEqual(linha['unitario_real'], Decimal('40.00'))

    def test_as_boas_saem_da_ultima_bancada_e_nao_da_quantidade_emitida(self):
        self._ficha(tecido_preco='0')
        ordem = self._ordem(quantidade=10)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, 4, produzido=9)
        self._etapa(ordem, EtapaOrdem.Etapa.ACABAMENTO, 7, produzido=7)

        self.assertEqual(self._linha()['boas'], 7)


class ResumoTests(CustoBase):

    def test_soma_as_ordens_e_separa_tecido_de_mao_de_obra(self):
        """
        Onde estourou é pergunta diferente de quanto: tecido se conserta no
        encaixe e na mesa, mão de obra se conserta na bancada.
        """
        self._ficha(tecido_consumo='1', tecido_preco='20')
        self._roteiro([(Operacao.Setor.COSTURA, 10, Operacao.TipoCusto.POR_HORA, 60)])
        ordem = self._ordem(quantidade=10)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, 4, produzido=10)
        self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, 6, produzido=10,
                    minutos=Decimal('130'))
        self._corte(ordem, consumo=11)

        resumo = self._painel()['resumo']

        self.assertEqual(resumo['previsto'], Decimal('300.00'))
        self.assertEqual(resumo['real'], Decimal('350.00'))
        self.assertEqual(resumo['tecido'], Decimal('20.00'))
        self.assertEqual(resumo['obra'], Decimal('30.00'))
        self.assertEqual(resumo['estouraram'], 1)

    def test_periodo_vazio_nao_estoura(self):
        resumo = self._painel()['resumo']

        self.assertEqual(resumo['ordens'], 0)
        self.assertIsNone(resumo['pior'])
        self.assertIsNone(resumo['variacao_pct'])


class TelaCustoTests(TestCase):
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
        resposta = self.client.get(reverse('moda:indicador-custos'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Nenhuma ordem concluída no período')

    def test_a_tela_abre_com_uma_ordem_que_estourou(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Time', cpf_cnpj='12345678901',
        )
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
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
            pedido=pedido, produto=produto, descricao='Camisa', quantidade=10,
        )
        ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            status=OrdemProducao.Status.CONCLUIDA,
            numero='OP-0001', ano=2026, sequencial=1, quantidade=10,
        )
        EtapaOrdem.objects.create(
            ordem=ordem, etapa=EtapaOrdem.Etapa.CORTE, sequencia=4,
            status=EtapaOrdem.Status.CONCLUIDA, quantidade_produzida=10,
            data_conclusao=timezone.localdate(),
        )
        Tecido.objects.create(filial=self.filial, nome='Malha Dry')
        RegistroCorte.objects.create(
            filial=self.filial, ordem=ordem, numero=1,
            status=RegistroCorte.Status.CORTADO, quantidade=10,
            data=timezone.localdate(), consumo_real=Decimal('13'),
        )

        resposta = self.client.get(reverse('moda:indicador-custos'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'OP-0001')
        # Previsto 200, real 260 -> estouro de 60.
        self.assertContains(resposta, '60,00')

    def test_periodo_invalido_cai_no_padrao_em_vez_de_estourar(self):
        resposta = self.client.get(
            reverse('moda:indicador-custos'), {'dias': 'trinta'},
        )

        self.assertEqual(resposta.status_code, 200)

    def test_a_rota_do_menu_cai_na_tela(self):
        from apps.moda.views_custo_real import CustoIndicadorView

        for url in (
            reverse('moda:indicador-custos'),
            reverse('moda:item', args=['indicadores', 'custos']),
        ):
            self.assertIs(resolve(url).func.view_class, CustoIndicadorView)

    def test_a_tela_de_engenharia_continua_sendo_outra(self):
        """
        `engenharia/custos/` responde "quanto este produto deveria custar";
        esta responde "quanto as ordens custaram". Confundir os endereços
        faria uma sumir do menu.
        """
        from apps.moda.views_custos import CustoListView

        self.assertIs(
            resolve(reverse('moda:custos')).func.view_class, CustoListView,
        )
