"""
Indicador de eficiência — minutos ganhos contra minutos disponíveis.

Este indicador tem uma família de jeitos de mentir, e é ela que os testes
abaixo cercam:

  · dividir por zero disfarçado — etapa sem cronômetro entra com tempo zero
    e a eficiência vai ao infinito;
  · contar como 0% o que na verdade é "não sei" — peça sem roteiro, setor
    sem capacidade cadastrada. Falta de cadastro derrubando o número da
    fábrica é como o indicador perde a confiança de quem lê;
  · somar minuto de produto errado — o roteiro é por OPERAÇÃO e a
    capacidade é por SETOR, e é preciso somar as operações dentro do setor
    antes de comparar.

Os números escolhidos são redondos de propósito: 10 min/peça, 20 peças,
200 minutos ganhos. Quando um teste falha, a conta cabe na cabeça.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.moda.models import (
    CapacidadeSetor, EtapaOrdem, ItemPedidoProducao, Operacao, OperacaoRoteiro,
    OrdemProducao, PedidoProducao, ProdutoModa, Roteiro,
)
from apps.moda.services.eficiencia import EficienciaService, minutos_por_setor


class EficienciaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Efic LTDA', nome_fantasia='Efic',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Efic LTDA',
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

    def _roteiro(self, produto, operacoes):
        """`operacoes` é uma lista de (setor, minutos por peça)."""
        roteiro = Roteiro.objects.create(filial=self.filial, produto=produto)
        for i, (setor, minutos) in enumerate(operacoes, start=1):
            operacao = Operacao.objects.create(
                filial=self.filial, nome=f'{setor}-{produto.codigo}-{i}',
                setor=setor, tempo_padrao=Decimal(minutos),
            )
            OperacaoRoteiro.objects.create(
                roteiro=roteiro, operacao=operacao, sequencia=i * 10,
            )
        return roteiro

    def _ordem(self, produto=None, quantidade=20, sequencial=1):
        item = ItemPedidoProducao.objects.create(
            pedido=self.pedido, produto=produto or self.produto,
            descricao='Camisa', quantidade=quantidade,
        )
        return OrdemProducao.objects.create(
            filial=self.filial, pedido=self.pedido, item=item,
            numero=f'OP-{sequencial:04d}', ano=2026, sequencial=sequencial,
            quantidade=quantidade,
        )

    def _etapa(self, ordem, etapa, produzido, minutos=None, dias_atras=1):
        return EtapaOrdem.objects.create(
            ordem=ordem, etapa=etapa, sequencia=10,
            status=EtapaOrdem.Status.CONCLUIDA,
            quantidade_produzida=produzido, tempo_minutos=minutos,
            data_conclusao=timezone.localdate() - timedelta(days=dias_atras),
        )

    def _capacidade(self, setor, horas_dia=8, postos=1, dias_semana=7,
                    eficiencia=100):
        """
        Padrão de sete dias por semana e 100% de eficiência de propósito:
        assim `minutos_disponiveis` é exatamente horas × 60 × dias, e a
        conta do teste não depende da aproximação de dias úteis.
        """
        return CapacidadeSetor.objects.create(
            filial=self.filial, setor=setor, postos=postos,
            horas_dia=Decimal(horas_dia), dias_semana=dias_semana,
            eficiencia=Decimal(eficiencia),
        )

    def _painel(self, dias=30):
        return EficienciaService.painel(self.filial, dias)

    @staticmethod
    def _por_setor(painel):
        return {l['setor']: l for l in painel['linhas']}

    # ── O roteiro por operação vira minuto por setor ─────────────────────

    def test_soma_as_operacoes_dentro_do_setor(self):
        """
        Duas operações de costura no roteiro são UM setor de costura na
        capacidade — comparar "pregar gola" com a costura inteira daria um
        número sem significado.
        """
        roteiro = self._roteiro(self.produto, [
            (Operacao.Setor.COSTURA, 6),
            (Operacao.Setor.COSTURA, 4),
            (Operacao.Setor.CORTE, 3),
        ])

        minutos = minutos_por_setor(roteiro)

        self.assertEqual(minutos[Operacao.Setor.COSTURA], Decimal('10'))
        self.assertEqual(minutos[Operacao.Setor.CORTE], Decimal('3'))

    def test_a_linha_do_roteiro_sobrescreve_o_tempo_do_catalogo(self):
        """A herança é por leitura: quem manda é o valor da linha, se houver."""
        roteiro = self._roteiro(self.produto, [(Operacao.Setor.COSTURA, 10)])
        linha = roteiro.etapas.first()
        linha.tempo_padrao = Decimal('4')
        linha.save(update_fields=['tempo_padrao'])

        self.assertEqual(
            minutos_por_setor(roteiro)[Operacao.Setor.COSTURA], Decimal('4'),
        )

    # ── Uso da capacidade ────────────────────────────────────────────────

    def test_uso_da_capacidade_e_ganho_sobre_disponivel(self):
        """20 peças × 10 min = 200 min ganhos, contra 8h/dia × 1 dia."""
        self._roteiro(self.produto, [(Operacao.Setor.COSTURA, 10)])
        self._capacidade(Operacao.Setor.COSTURA, horas_dia=8)
        ordem = self._ordem(quantidade=20)
        self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, produzido=20)

        linha = self._por_setor(self._painel(dias=1))[Operacao.Setor.COSTURA]

        self.assertEqual(linha['ganho'], Decimal('200'))
        self.assertEqual(linha['disponivel'], Decimal('480.00'))
        self.assertEqual(linha['uso'], Decimal('41.7'))

    def test_so_a_peca_boa_gera_credito(self):
        """
        A perda entra na eficiência por aqui, sem precisar de coluna: quem
        produziu 15 de 20 ganhou 150 minutos, não 200.
        """
        self._roteiro(self.produto, [(Operacao.Setor.COSTURA, 10)])
        self._capacidade(Operacao.Setor.COSTURA)
        ordem = self._ordem(quantidade=20)
        etapa = self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, produzido=15)
        etapa.perda = 5
        etapa.save(update_fields=['perda'])

        linha = self._por_setor(self._painel())[Operacao.Setor.COSTURA]

        self.assertEqual(linha['ganho'], Decimal('150'))

    def test_passar_da_capacidade_e_marcado_e_a_barra_para_em_100(self):
        """
        Uso acima de 100% é hora extra ou cadastro desatualizado — nos dois
        casos precisa aparecer. A barra para em 100 só para não sair da
        tela; quem guarda o fato é `estourou`.
        """
        self._roteiro(self.produto, [(Operacao.Setor.COSTURA, 60)])
        self._capacidade(Operacao.Setor.COSTURA, horas_dia=8)
        ordem = self._ordem(quantidade=20)
        self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, produzido=20)

        linha = self._por_setor(self._painel(dias=1))[Operacao.Setor.COSTURA]

        self.assertEqual(linha['uso'], Decimal('250.0'))
        self.assertTrue(linha['estourou'])
        self.assertEqual(linha['barra'], 100)
        # Nada de ocioso negativo: a folga acabou, não virou dívida.
        self.assertEqual(linha['ocioso'], Decimal('0'))

    # ── Eficiência ───────────────────────────────────────────────────────

    def test_eficiencia_e_ganho_sobre_o_tempo_apontado(self):
        """200 minutos de padrão feitos em 250 apontados = 80%."""
        self._roteiro(self.produto, [(Operacao.Setor.COSTURA, 10)])
        self._capacidade(Operacao.Setor.COSTURA)
        ordem = self._ordem(quantidade=20)
        self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, produzido=20,
                    minutos=Decimal('250'))

        linha = self._por_setor(self._painel())[Operacao.Setor.COSTURA]

        self.assertEqual(linha['eficiencia'], Decimal('80.0'))

    def test_etapa_sem_cronometro_nao_entra_na_eficiencia(self):
        """
        É o jeito mais fácil de este indicador mentir: somar o ganho de uma
        etapa cujo tempo ninguém apontou manda a razão para o infinito.

        A etapa cronometrada rendeu 100 min em 100 min (100%). A outra
        rendeu 100 min sem tempo nenhum. Se entrasse, daria 200%.
        """
        self._roteiro(self.produto, [(Operacao.Setor.COSTURA, 10)])
        self._capacidade(Operacao.Setor.COSTURA)
        cronometrada = self._ordem(quantidade=10, sequencial=1)
        solta = self._ordem(quantidade=10, sequencial=2)
        self._etapa(cronometrada, EtapaOrdem.Etapa.COSTURA, produzido=10,
                    minutos=Decimal('100'))
        self._etapa(solta, EtapaOrdem.Etapa.COSTURA, produzido=10)

        linha = self._por_setor(self._painel())[Operacao.Setor.COSTURA]

        self.assertEqual(linha['eficiencia'], Decimal('100.0'))
        # O uso da capacidade continua contando as duas: ele não depende de
        # cronômetro nenhum, só do roteiro.
        self.assertEqual(linha['ganho'], Decimal('200'))
        self.assertEqual(linha['pecas'], 20)

    def test_sem_ninguem_cronometrar_a_eficiencia_e_traco_e_nao_zero(self):
        """
        Zero por cento diria "a fábrica está parada". O certo é "não sei",
        e a tela mostra traço.
        """
        self._roteiro(self.produto, [(Operacao.Setor.COSTURA, 10)])
        self._capacidade(Operacao.Setor.COSTURA)
        ordem = self._ordem(quantidade=20)
        self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, produzido=20)

        painel = self._painel()

        self.assertIsNone(self._por_setor(painel)[Operacao.Setor.COSTURA]['eficiencia'])
        self.assertIsNone(painel['resumo']['eficiencia'])
        self.assertTrue(painel['resumo']['sem_apontamento'])

    # ── Falta de cadastro não pode virar número ruim ─────────────────────

    def test_peca_sem_roteiro_fica_de_fora_e_e_declarada(self):
        """
        Contá-la como zero derrubaria o indicador por falta de cadastro, e
        não por queda de ritmo — e ninguém confia num número desses.
        """
        self._roteiro(self.produto, [(Operacao.Setor.COSTURA, 10)])
        self._capacidade(Operacao.Setor.COSTURA)
        sem_roteiro = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM002', nome='Camisa sem roteiro',
        )
        com = self._ordem(quantidade=20, sequencial=1)
        sem = self._ordem(produto=sem_roteiro, quantidade=5, sequencial=2)
        self._etapa(com, EtapaOrdem.Etapa.COSTURA, produzido=20)
        self._etapa(sem, EtapaOrdem.Etapa.COSTURA, produzido=5)

        linha = self._por_setor(self._painel())[Operacao.Setor.COSTURA]

        self.assertEqual(linha['ganho'], Decimal('200'))
        self.assertEqual(linha['pecas'], 25)
        self.assertEqual(linha['pecas_com_padrao'], 20)
        self.assertEqual(linha['sem_padrao'], 5)
        self.assertEqual(linha['cobertura'], Decimal('80.0'))

    def test_setor_sem_capacidade_mostra_traco_e_nao_estouro(self):
        """
        Sem capacidade cadastrada não há denominador. Zero ali daria uso
        infinito e apontaria um gargalo que talvez nem exista.
        """
        self._roteiro(self.produto, [(Operacao.Setor.COSTURA, 10)])
        ordem = self._ordem(quantidade=20)
        self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, produzido=20)

        painel = self._painel()
        linha = self._por_setor(painel)[Operacao.Setor.COSTURA]

        self.assertFalse(linha['tem_capacidade'])
        self.assertIsNone(linha['uso'])
        self.assertFalse(linha['estourou'])
        self.assertTrue(painel['sem_capacidade'])

    def test_setor_com_capacidade_e_sem_producao_aparece_ocioso(self):
        """
        Capacidade parada é justamente o que este indicador existe para
        mostrar: some-la da tela esconderia o problema.
        """
        self._capacidade(Operacao.Setor.CORTE, horas_dia=8)

        linha = self._por_setor(self._painel(dias=1))[Operacao.Setor.CORTE]

        self.assertEqual(linha['pecas'], 0)
        self.assertEqual(linha['uso'], Decimal('0.0'))
        self.assertEqual(linha['ocioso'], Decimal('480.00'))

    def test_setor_sem_capacidade_e_sem_producao_nao_vira_linha(self):
        self._capacidade(Operacao.Setor.COSTURA)

        setores = [l['setor'] for l in self._painel()['linhas']]

        self.assertEqual(setores, [Operacao.Setor.COSTURA])

    # ── Recorte ──────────────────────────────────────────────────────────

    def test_a_ordem_das_linhas_e_a_do_fluxo(self):
        for setor in (Operacao.Setor.EXPEDICAO, Operacao.Setor.COSTURA,
                      Operacao.Setor.CORTE):
            self._capacidade(setor)

        self.assertEqual(
            [l['setor'] for l in self._painel()['linhas']],
            [Operacao.Setor.CORTE, Operacao.Setor.COSTURA,
             Operacao.Setor.EXPEDICAO],
        )

    def test_etapa_administrativa_nao_tem_setor_e_nao_entra(self):
        """
        Planejamento não é bancada. Sem par no mapa de setores, a etapa nem
        chega à conta.
        """
        self._roteiro(self.produto, [(Operacao.Setor.COSTURA, 10)])
        self._capacidade(Operacao.Setor.COSTURA)
        ordem = self._ordem(quantidade=20)
        self._etapa(ordem, EtapaOrdem.Etapa.PLANEJAMENTO, produzido=20)

        linha = self._por_setor(self._painel())[Operacao.Setor.COSTURA]

        self.assertEqual(linha['pecas'], 0)

    def test_conclusao_fora_da_janela_nao_conta(self):
        self._roteiro(self.produto, [(Operacao.Setor.COSTURA, 10)])
        self._capacidade(Operacao.Setor.COSTURA)
        ordem = self._ordem(quantidade=20)
        self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, produzido=20, dias_atras=60)

        self.assertEqual(
            self._por_setor(self._painel(dias=30))[Operacao.Setor.COSTURA]['pecas'], 0,
        )
        self.assertEqual(
            self._por_setor(self._painel(dias=90))[Operacao.Setor.COSTURA]['pecas'], 20,
        )

    def test_etapa_nao_concluida_nao_conta(self):
        self._roteiro(self.produto, [(Operacao.Setor.COSTURA, 10)])
        self._capacidade(Operacao.Setor.COSTURA)
        ordem = self._ordem(quantidade=20)
        etapa = self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, produzido=20)
        etapa.status = EtapaOrdem.Status.EM_ANDAMENTO
        etapa.save(update_fields=['status'])

        self.assertEqual(
            self._por_setor(self._painel())[Operacao.Setor.COSTURA]['pecas'], 0,
        )

    # ── Capacidade em minutos ────────────────────────────────────────────

    def test_a_eficiencia_do_cadastro_desconta_da_capacidade(self):
        """
        Ninguém produz 100% da jornada. 8h a 85% são 408 minutos por dia,
        e é esse o denominador honesto.
        """
        self._capacidade(Operacao.Setor.COSTURA, horas_dia=8, eficiencia=85)

        linha = self._por_setor(self._painel(dias=1))[Operacao.Setor.COSTURA]

        self.assertEqual(linha['disponivel'], Decimal('408.00'))

    def test_os_postos_multiplicam_a_capacidade(self):
        self._capacidade(Operacao.Setor.COSTURA, horas_dia=8, postos=3)

        linha = self._por_setor(self._painel(dias=1))[Operacao.Setor.COSTURA]

        self.assertEqual(linha['disponivel'], Decimal('1440.00'))

    def test_semana_de_cinco_dias_nao_conta_o_fim_de_semana(self):
        """
        Sete dias corridos com jornada de segunda a sexta são cinco dias de
        capacidade, não sete.
        """
        self._capacidade(Operacao.Setor.COSTURA, horas_dia=8, dias_semana=5)

        linha = self._por_setor(self._painel(dias=7))[Operacao.Setor.COSTURA]

        self.assertEqual(linha['disponivel'], Decimal('2400.00'))

    # ── Cabeçalho ────────────────────────────────────────────────────────

    def test_o_setor_mais_apertado_e_o_de_maior_uso(self):
        """É ele que satura primeiro e segura a fábrica inteira."""
        self._roteiro(self.produto, [
            (Operacao.Setor.CORTE, 2), (Operacao.Setor.COSTURA, 20),
        ])
        self._capacidade(Operacao.Setor.CORTE, horas_dia=8)
        self._capacidade(Operacao.Setor.COSTURA, horas_dia=8)
        ordem = self._ordem(quantidade=20)
        self._etapa(ordem, EtapaOrdem.Etapa.CORTE, produzido=20)
        self._etapa(ordem, EtapaOrdem.Etapa.COSTURA, produzido=20)

        resumo = self._painel(dias=1)['resumo']

        self.assertEqual(resumo['apertado']['setor'], Operacao.Setor.COSTURA)

    def test_periodo_vazio_nao_estoura(self):
        painel = self._painel()

        self.assertEqual(painel['linhas'], [])
        self.assertIsNone(painel['resumo']['uso'])
        self.assertIsNone(painel['resumo']['apertado'])
        self.assertEqual(painel['resumo']['pecas'], 0)

    # ── Tela ─────────────────────────────────────────────────────────────

    def test_a_rota_do_menu_cai_na_tela(self):
        from apps.moda.views_eficiencia import EficienciaIndicadorView

        for url in (
            reverse('moda:indicador-eficiencia'),
            reverse('moda:item', args=['indicadores', 'eficiencia']),
        ):
            self.assertIs(resolve(url).func.view_class, EficienciaIndicadorView)


class TelaEficienciaTests(TestCase):
    """
    A TELA renderizando de verdade, e não só o serviço.

    Os testes de cima provam a conta; nenhum deles abriria o template. Um
    `{% if %}` errado ou um binding do Alpine mal fechado passa por eles e
    derruba a página em produção — foi assim que esta tela já caiu antes.
    Por isso aqui o pedido é HTTP mesmo, com login e middleware no caminho.
    """

    @classmethod
    def setUpTestData(cls):
        from apps.core.models import PerfilAcesso, Usuario

        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Tela LTDA', nome_fantasia='Tela',
            cnpj='53345678000191',
            # Sem o segmento o middleware barra /moda/ antes da view, e o
            # teste passaria a medir o redirect em vez da tela.
            segmento='moda_confeccao',
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
        """
        O estado mais comum numa fábrica que acabou de ligar o sistema — e
        o que mais quebra, porque tudo é vazio ou None.
        """
        resposta = self.client.get(reverse('moda:indicador-eficiencia'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Nenhum setor tem capacidade cadastrada')

    def test_a_tela_abre_com_dado(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Time', cpf_cnpj='12345678901',
        )
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        roteiro = Roteiro.objects.create(filial=self.filial, produto=produto)
        operacao = Operacao.objects.create(
            filial=self.filial, nome='Costura', setor=Operacao.Setor.COSTURA,
            tempo_padrao=Decimal('10'),
        )
        OperacaoRoteiro.objects.create(roteiro=roteiro, operacao=operacao, sequencia=10)
        CapacidadeSetor.objects.create(
            filial=self.filial, setor=Operacao.Setor.COSTURA,
            postos=1, horas_dia=Decimal('8'), dias_semana=5,
            eficiencia=Decimal('85'),
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=cliente, numero=1,
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa', quantidade=20,
        )
        ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            numero='OP-0001', ano=2026, sequencial=1, quantidade=20,
        )
        EtapaOrdem.objects.create(
            ordem=ordem, etapa=EtapaOrdem.Etapa.COSTURA, sequencia=10,
            status=EtapaOrdem.Status.CONCLUIDA, quantidade_produzida=20,
            tempo_minutos=Decimal('250'),
            data_conclusao=timezone.localdate(),
        )

        resposta = self.client.get(reverse('moda:indicador-eficiencia'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Costura')
        self.assertContains(resposta, '80,0%')

    def test_periodo_invalido_cai_no_padrao_em_vez_de_estourar(self):
        """`?dias=` vem da barra de endereços e chega com qualquer coisa."""
        resposta = self.client.get(
            reverse('moda:indicador-eficiencia'), {'dias': 'trinta'},
        )

        self.assertEqual(resposta.status_code, 200)
