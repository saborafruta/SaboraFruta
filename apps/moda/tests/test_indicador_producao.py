"""
Indicador de produção — volume por etapa.

O risco aqui não é a tela quebrar: é ela mostrar um número plausível e
errado, que ninguém confere porque parece certo. Os dois jeitos de errar
são contar o que não devia (etapa administrativa, período fora da janela) e
somar a coluna errada (produzido no lugar do recebido).

A CADEIA DE QUANTIDADE é o que dá sentido ao número: `planejada` herda o
produzido da etapa anterior, e é isso que faz a perda do corte aparecer no
recebido da costura. Se essa herança quebrar, o relatório mente sem avisar.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.moda.models import EtapaOrdem, ItemPedidoProducao, OrdemProducao, PedidoProducao
from apps.moda.views_producao import resumir


class IndicadorProducaoTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Ind LTDA', nome_fantasia='Ind',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Ind LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Time', cpf_cnpj='12345678901',
        )

    def setUp(self):
        self.pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=1,
        )
        self.item = ItemPedidoProducao.objects.create(
            pedido=self.pedido, descricao='Camisa', quantidade=100,
        )
        self.ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=self.pedido, item=self.item,
            numero='OP-0001', ano=2026, sequencial=1, quantidade=100,
        )

    def _etapa(self, nome, sequencia, produzido, perda=0, dias_atras=1,
               status=EtapaOrdem.Status.CONCLUIDA):
        return EtapaOrdem.objects.create(
            ordem=self.ordem, etapa=nome, sequencia=sequencia, status=status,
            quantidade_produzida=produzido, perda=perda,
            data_conclusao=timezone.localdate() - timedelta(days=dias_atras),
        )

    def _linhas(self, dias=30):
        desde = timezone.localdate() - timedelta(days=dias)
        return resumir(
            EtapaOrdem.objects.filter(
                ordem__filial=self.filial,
                status=EtapaOrdem.Status.CONCLUIDA,
                data_conclusao__gte=desde,
            ).select_related('ordem').prefetch_related('ordem__etapas').order_by('sequencia')
        )

    # ── A cadeia entre bancadas ──────────────────────────────────────────

    def test_o_que_uma_etapa_recebe_e_o_que_a_anterior_entregou(self):
        """
        É o que dá sentido ao volume: 90 na costura é saudável se ela
        recebeu 90, e é perda se recebeu 100.
        """
        self._etapa(EtapaOrdem.Etapa.CORTE, 4, produzido=90, dias_atras=3)
        self._etapa(EtapaOrdem.Etapa.COSTURA, 6, produzido=90, dias_atras=1)

        linhas = {l['etapa']: l for l in self._linhas()}

        # O corte recebeu as 100 da ordem e entregou 90.
        self.assertEqual(linhas['corte']['planejado'], 100)
        self.assertEqual(linhas['corte']['perdido'], 10)
        # A costura recebeu as 90 que o corte entregou, e não as 100.
        self.assertEqual(linhas['costura']['planejado'], 90)
        self.assertEqual(linhas['costura']['perdido'], 0)

    def test_ordem_do_fluxo_e_nao_por_volume(self):
        """
        A leitura é a peça descendo a fábrica: ordenar por quantidade faria
        a queda entre bancadas desaparecer.
        """
        self._etapa(EtapaOrdem.Etapa.COSTURA, 6, produzido=50)
        self._etapa(EtapaOrdem.Etapa.CORTE, 4, produzido=100)

        self.assertEqual(
            [l['etapa'] for l in self._linhas()], ['corte', 'costura'],
        )

    # ── O que não pode entrar ────────────────────────────────────────────

    def test_etapa_administrativa_fica_de_fora(self):
        """
        Planejamento não é bancada: contá-lo faria parecer que alguém
        produziu cem peças sem tocar em tecido.
        """
        self._etapa(EtapaOrdem.Etapa.PLANEJAMENTO, 2, produzido=100)
        self._etapa(EtapaOrdem.Etapa.CORTE, 4, produzido=100)

        self.assertEqual([l['etapa'] for l in self._linhas()], ['corte'])

    def test_etapa_nao_concluida_nao_conta(self):
        """Volume é o que SAIU da bancada, não o que está em cima dela."""
        self._etapa(
            EtapaOrdem.Etapa.CORTE, 4, produzido=100,
            status=EtapaOrdem.Status.EM_ANDAMENTO,
        )

        self.assertEqual(self._linhas(), [])

    def test_conclusao_fora_da_janela_nao_conta(self):
        self._etapa(EtapaOrdem.Etapa.CORTE, 4, produzido=100, dias_atras=60)

        self.assertEqual(self._linhas(dias=30), [])
        self.assertEqual(len(self._linhas(dias=90)), 1)

    # ── Somas ────────────────────────────────────────────────────────────

    def test_soma_as_ordens_da_mesma_etapa(self):
        outra = OrdemProducao.objects.create(
            filial=self.filial, pedido=self.pedido, item=self.item,
            numero='OP-0002', ano=2026, sequencial=2, quantidade=50,
        )
        self._etapa(EtapaOrdem.Etapa.CORTE, 4, produzido=100)
        EtapaOrdem.objects.create(
            ordem=outra, etapa=EtapaOrdem.Etapa.CORTE, sequencia=4,
            status=EtapaOrdem.Status.CONCLUIDA, quantidade_produzida=50,
            data_conclusao=timezone.localdate(),
        )

        linha = self._linhas()[0]

        self.assertEqual(linha['ordens'], 2)
        self.assertEqual(linha['produzido'], 150)
        self.assertEqual(linha['planejado'], 150)

    def test_a_barra_compara_as_bancadas_entre_si(self):
        """A maior etapa vale 100%; as outras se medem contra ela."""
        self._etapa(EtapaOrdem.Etapa.CORTE, 4, produzido=100)
        self._etapa(EtapaOrdem.Etapa.COSTURA, 6, produzido=50)

        linhas = {l['etapa']: l for l in self._linhas()}

        self.assertEqual(linhas['corte']['barra'], 100)
        self.assertEqual(linhas['costura']['barra'], 50)

    def test_sem_nada_no_periodo_devolve_lista_vazia(self):
        self.assertEqual(self._linhas(), [])

    # ── Rota ─────────────────────────────────────────────────────────────

    def test_a_rota_do_menu_cai_na_tela(self):
        from django.urls import resolve, reverse

        from apps.moda.views_producao import ProducaoIndicadorView

        for url in (
            reverse('moda:indicador-producao'),
            reverse('moda:item', args=['indicadores', 'producao']),
        ):
            self.assertIs(resolve(url).func.view_class, ProducaoIndicadorView)
