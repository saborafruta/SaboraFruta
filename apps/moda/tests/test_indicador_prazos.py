"""
Prazos — o que chegou na data combinada, e o que ainda vai chegar tarde.

O jeito mais comum de um indicador de prazo mentir é a MÉDIA COM SINAL:
entregar dez dias adiantado para um cliente e dez atrasado para outro dá
zero, e a fábrica que deixou os dois insatisfeitos aparece perfeita. Por
isso o placar é contagem e o atraso médio só olha os atrasados.

O segundo é contar como "no prazo" o pedido que nunca teve prazo. Ele
inflaria o indicador com pedido que ninguém prometeu — e é justamente onde
o atraso costuma se esconder.

Os testes também cercam a data real: ela vem do aceite do cliente quando
existe, e um pedido com várias ordens só está entregue quando a ÚLTIMA
chega. Pegar a primeira diria que chegou no prazo o que chegou pela metade.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.moda.models import (
    EtapaOrdem, Expedicao, ItemPedidoProducao, OrdemProducao, PedidoProducao,
    ProdutoModa,
)
from apps.moda.services.prazo import JANELA_RISCO, PrazoService, faixa_do_atraso


class PrazoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Prazo LTDA', nome_fantasia='Prazo',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Prazo LTDA',
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
        self.hoje = timezone.localdate()

    # ── Montagem ─────────────────────────────────────────────────────────

    def _pedido(self, prazo_dias=None, status=PedidoProducao.Status.ENTREGUE):
        self._n += 1
        return PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=self._n,
            status=status,
            data_prevista_entrega=(
                None if prazo_dias is None
                else self.hoje + timedelta(days=prazo_dias)
            ),
        )

    def _ordem(self, pedido, quantidade=100):
        self._n += 1
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=self.produto, descricao='Camisa',
            quantidade=quantidade,
        )
        return OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            numero=f'OP-{self._n:04d}', ano=2026, sequencial=self._n,
            quantidade=quantidade,
        )

    def _entregue_pela_expedicao(self, ordem, dias_atras):
        self._n += 1
        return Expedicao.objects.create(
            filial=self.filial, ordem=ordem, numero=self._n,
            status=Expedicao.Status.ENTREGA,
            data_entrega=timezone.now() - timedelta(days=dias_atras),
        )

    def _entregue_pelo_fluxo(self, ordem, dias_atras):
        return EtapaOrdem.objects.create(
            ordem=ordem, etapa=EtapaOrdem.Etapa.ENTREGA, sequencia=11,
            status=EtapaOrdem.Status.CONCLUIDA,
            data_conclusao=self.hoje - timedelta(days=dias_atras),
        )

    def _painel(self, dias=30):
        return PrazoService.painel(self.filial, dias)


class FaixaTests(TestCase):
    """A distribuição existe porque a média esconde a cauda."""

    def test_no_prazo_inclui_o_adiantado_e_o_do_dia(self):
        self.assertEqual(faixa_do_atraso(-5), 'no_prazo')
        self.assertEqual(faixa_do_atraso(0), 'no_prazo')

    def test_os_cortes_das_faixas(self):
        self.assertEqual(faixa_do_atraso(1), 'ate_3')
        self.assertEqual(faixa_do_atraso(3), 'ate_3')
        self.assertEqual(faixa_do_atraso(4), 'ate_7')
        self.assertEqual(faixa_do_atraso(7), 'ate_7')
        self.assertEqual(faixa_do_atraso(8), 'ate_15')
        self.assertEqual(faixa_do_atraso(15), 'ate_15')

    def test_a_ultima_faixa_e_aberta(self):
        """Atraso de mês não tem teto e não pode ser diluído no de três dias."""
        self.assertEqual(faixa_do_atraso(16), 'acima_15')
        self.assertEqual(faixa_do_atraso(300), 'acima_15')


class PlacarTests(PrazoBase):
    """O que já foi entregue."""

    def test_entrega_antes_do_prazo_conta_como_no_prazo(self):
        pedido = self._pedido(prazo_dias=-5)
        self._entregue_pelo_fluxo(self._ordem(pedido), dias_atras=10)

        placar = self._painel()['placar']

        self.assertEqual(placar['entregues'], 1)
        self.assertEqual(placar['no_prazo'], 1)
        self.assertIsNone(placar['atraso_medio'])

    def test_entrega_depois_do_prazo_conta_o_desvio(self):
        pedido = self._pedido(prazo_dias=-10)
        self._entregue_pelo_fluxo(self._ordem(pedido), dias_atras=3)

        linha = self._painel()['entregues'][0]

        self.assertTrue(linha['atrasado'])
        self.assertEqual(linha['desvio'], 7)

    def test_antecipacao_nao_compensa_atraso(self):
        """
        O erro clássico: media com sinal daria zero numa fábrica que deixou
        os dois clientes insatisfeitos.
        """
        adiantado = self._pedido(prazo_dias=-1)
        atrasado = self._pedido(prazo_dias=-11)
        self._entregue_pelo_fluxo(self._ordem(adiantado), dias_atras=11)
        self._entregue_pelo_fluxo(self._ordem(atrasado), dias_atras=1)

        placar = self._painel()['placar']

        self.assertEqual(placar['no_prazo'], 1)
        self.assertEqual(placar['atrasados'], 1)
        self.assertEqual(placar['otd'], 50)
        # E o atraso médio NÃO é zero.
        self.assertEqual(placar['atraso_medio'], 10)

    def test_o_atraso_medio_so_olha_os_atrasados(self):
        """
        Incluir quem chegou em dia dividiria o atraso por quem não atrasou:
        trinta dias em um de dez pedidos viraria três.
        """
        for _ in range(9):
            pedido = self._pedido(prazo_dias=-5)
            self._entregue_pelo_fluxo(self._ordem(pedido), dias_atras=5)
        atrasado = self._pedido(prazo_dias=-35)
        self._entregue_pelo_fluxo(self._ordem(atrasado), dias_atras=5)

        placar = self._painel()['placar']

        self.assertEqual(placar['atrasados'], 1)
        self.assertEqual(placar['atraso_medio'], 30)

    def test_pedido_sem_prazo_fica_fora_do_placar(self):
        """
        Contá-lo como "no prazo" inflaria o indicador com pedido que ninguém
        prometeu.
        """
        pedido = self._pedido(prazo_dias=None)
        self._entregue_pelo_fluxo(self._ordem(pedido), dias_atras=1)

        placar = self._painel()['placar']

        self.assertEqual(placar['entregues'], 0)
        self.assertEqual(placar['sem_prazo'], 1)
        self.assertIsNone(placar['otd'])

    def test_entregue_sem_carimbo_de_data_e_declarado(self):
        """Sem a data real não dá para dizer se chegou no prazo."""
        pedido = self._pedido(prazo_dias=-5)
        self._ordem(pedido)

        placar = self._painel()['placar']

        self.assertEqual(placar['entregues'], 0)
        self.assertEqual(placar['sem_data'], 1)

    def test_a_janela_e_pela_entrega_real(self):
        """Do que SAIU no período, quanto chegou no prazo."""
        pedido = self._pedido(prazo_dias=-100)
        self._entregue_pelo_fluxo(self._ordem(pedido), dias_atras=60)

        self.assertEqual(self._painel(dias=30)['placar']['entregues'], 0)
        self.assertEqual(self._painel(dias=90)['placar']['entregues'], 1)

    def test_o_pior_e_o_de_maior_atraso(self):
        leve = self._pedido(prazo_dias=-3)
        grave = self._pedido(prazo_dias=-40)
        self._entregue_pelo_fluxo(self._ordem(leve), dias_atras=1)
        self._entregue_pelo_fluxo(self._ordem(grave), dias_atras=1)

        placar = self._painel()['placar']

        self.assertEqual(placar['pior']['desvio'], 39)

    def test_a_distribuicao_separa_a_cauda(self):
        curto = self._pedido(prazo_dias=-3)
        longo = self._pedido(prazo_dias=-31)
        self._entregue_pelo_fluxo(self._ordem(curto), dias_atras=1)
        self._entregue_pelo_fluxo(self._ordem(longo), dias_atras=1)

        faixas = {f['chave']: f for f in self._painel()['faixas']}

        self.assertEqual(faixas['ate_3']['pedidos'], 1)
        self.assertEqual(faixas['acima_15']['pedidos'], 1)
        self.assertEqual(faixas['no_prazo']['pedidos'], 0)


class DataDeEntregaTests(PrazoBase):
    """De onde sai o "quando chegou"."""

    def test_o_aceite_do_cliente_e_a_data(self):
        pedido = self._pedido(prazo_dias=-10)
        self._entregue_pela_expedicao(self._ordem(pedido), dias_atras=2)

        linha = self._painel()['entregues'][0]

        self.assertEqual(linha['real'], self.hoje - timedelta(days=2))
        self.assertEqual(linha['desvio'], 8)

    def test_sem_aceite_vale_a_etapa_do_fluxo(self):
        pedido = self._pedido(prazo_dias=-10)
        self._entregue_pelo_fluxo(self._ordem(pedido), dias_atras=4)

        self.assertEqual(
            self._painel()['entregues'][0]['real'], self.hoje - timedelta(days=4),
        )

    def test_com_varias_ordens_vale_a_ultima_a_chegar(self):
        """
        Pegar a primeira diria que chegou no prazo o que chegou pela metade.
        """
        pedido = self._pedido(prazo_dias=-10)
        self._entregue_pelo_fluxo(self._ordem(pedido), dias_atras=9)
        self._entregue_pelo_fluxo(self._ordem(pedido), dias_atras=2)

        linha = self._painel()['entregues'][0]

        self.assertEqual(linha['real'], self.hoje - timedelta(days=2))
        self.assertEqual(linha['desvio'], 8)

    def test_expedicao_nao_entregue_nao_carimba_data(self):
        pedido = self._pedido(prazo_dias=-5)
        ordem = self._ordem(pedido)
        Expedicao.objects.create(
            filial=self.filial, ordem=ordem, numero=99,
            status=Expedicao.Status.DESPACHO,
            data_entrega=timezone.now(),
        )

        self.assertEqual(self._painel()['placar']['sem_data'], 1)


class RiscoTests(PrazoBase):
    """O que ainda dá para salvar."""

    def test_pedido_aberto_vencido_e_atrasado(self):
        pedido = self._pedido(
            prazo_dias=-4, status=PedidoProducao.Status.EM_PRODUCAO,
        )
        self._ordem(pedido)

        risco = self._painel()['risco']

        self.assertEqual(risco['atrasados'], 1)
        self.assertEqual(risco['pior']['atraso'], 4)

    def test_pedido_que_vence_dentro_da_janela_e_risco(self):
        pedido = self._pedido(
            prazo_dias=JANELA_RISCO - 1,
            status=PedidoProducao.Status.EM_PRODUCAO,
        )
        self._ordem(pedido)

        risco = self._painel()['risco']

        self.assertEqual(risco['vencem'], 1)
        self.assertEqual(risco['atrasados'], 0)

    def test_prazo_folgado_nao_e_risco(self):
        pedido = self._pedido(
            prazo_dias=JANELA_RISCO + 30,
            status=PedidoProducao.Status.EM_PRODUCAO,
        )
        self._ordem(pedido)

        risco = self._painel()['risco']

        self.assertEqual(risco['vencem'], 0)
        self.assertEqual(risco['atrasados'], 0)

    def test_orcamento_nao_e_compromisso(self):
        """Orçamento ainda não foi prometido a ninguém."""
        pedido = self._pedido(
            prazo_dias=-10, status=PedidoProducao.Status.ORCAMENTO,
        )
        self._ordem(pedido)

        self.assertEqual(self._painel()['risco']['abertos'], 0)

    def test_cancelado_e_entregue_saem_da_lista_de_aberto(self):
        for status in (PedidoProducao.Status.CANCELADO,
                       PedidoProducao.Status.ENTREGUE):
            pedido = self._pedido(prazo_dias=-10, status=status)
            self._ordem(pedido)

        self.assertEqual(self._painel()['risco']['abertos'], 0)

    def test_aberto_sem_prazo_aparece_e_vai_para_o_fim(self):
        """
        Ali não há urgência a calcular, e sim cadastro faltando — mas
        esconder o pedido seria pior: é onde o atraso se esconde.
        """
        urgente = self._pedido(
            prazo_dias=-5, status=PedidoProducao.Status.EM_PRODUCAO,
        )
        sem_prazo = self._pedido(
            prazo_dias=None, status=PedidoProducao.Status.EM_PRODUCAO,
        )
        self._ordem(urgente)
        self._ordem(sem_prazo)

        painel = self._painel()

        self.assertEqual(painel['risco']['sem_prazo'], 1)
        self.assertEqual(
            [l['numero'] for l in painel['abertos']],
            [urgente.numero, sem_prazo.numero],
        )

    def test_os_mais_urgentes_no_topo(self):
        muito = self._pedido(prazo_dias=-20, status=PedidoProducao.Status.EM_PRODUCAO)
        pouco = self._pedido(prazo_dias=-2, status=PedidoProducao.Status.EM_PRODUCAO)
        folgado = self._pedido(prazo_dias=40, status=PedidoProducao.Status.EM_PRODUCAO)
        for p in (muito, pouco, folgado):
            self._ordem(p)

        numeros = [l['numero'] for l in self._painel()['abertos']]

        self.assertEqual(numeros, [muito.numero, pouco.numero, folgado.numero])

    def test_sem_pedido_nenhum_nao_estoura(self):
        painel = self._painel()

        self.assertEqual(painel['abertos'], [])
        self.assertEqual(painel['entregues'], [])
        self.assertIsNone(painel['placar']['otd'])
        self.assertIsNone(painel['risco']['pior'])


class TelaPrazoTests(TestCase):
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
        resposta = self.client.get(reverse('moda:indicador-prazos'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Nenhum pedido em aberto')

    def test_a_tela_mostra_o_atraso_de_um_pedido_aberto(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Escola Sao Jose',
            cpf_cnpj='12345678901',
        )
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=cliente, numero=1,
            status=PedidoProducao.Status.EM_PRODUCAO,
            data_prevista_entrega=timezone.localdate() - timedelta(days=6),
        )
        ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa', quantidade=100,
        )

        resposta = self.client.get(reverse('moda:indicador-prazos'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Escola Sao Jose')
        self.assertContains(resposta, '6 dia(s) atrás')

    def test_a_tela_mostra_o_placar_de_uma_entrega_atrasada(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Time', cpf_cnpj='12345678901',
        )
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=cliente, numero=1,
            status=PedidoProducao.Status.ENTREGUE,
            data_prevista_entrega=timezone.localdate() - timedelta(days=12),
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa', quantidade=100,
        )
        ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            numero='OP-0001', ano=2026, sequencial=1, quantidade=100,
        )
        EtapaOrdem.objects.create(
            ordem=ordem, etapa=EtapaOrdem.Etapa.ENTREGA, sequencia=11,
            status=EtapaOrdem.Status.CONCLUIDA,
            data_conclusao=timezone.localdate() - timedelta(days=2),
        )

        resposta = self.client.get(reverse('moda:indicador-prazos'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, '0,0%')
        self.assertContains(resposta, '+10 dia(s)')
        self.assertContains(resposta, 'Mais de 15 dias')

    def test_periodo_invalido_cai_no_padrao(self):
        resposta = self.client.get(
            reverse('moda:indicador-prazos'), {'dias': 'trinta'},
        )

        self.assertEqual(resposta.status_code, 200)

    def test_a_rota_do_menu_cai_na_tela(self):
        from apps.moda.views_prazo import PrazoIndicadorView

        for url in (
            reverse('moda:indicador-prazos'),
            reverse('moda:item', args=['indicadores', 'prazos']),
        ):
            self.assertIs(resolve(url).func.view_class, PrazoIndicadorView)
