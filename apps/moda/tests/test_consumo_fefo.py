"""
A produção consome matéria-prima por FEFO, com rastro até o rolo.

A baixa do corte chamava `registrar_movimentacao(permitir_sem_lote=True)`: o
saldo do produto caía e nenhum lote era tocado. Num tecido com lote,
`Estoque.quantidade_atual` descia e `LoteProduto.quantidade_atual` ficava
cheio — os dois números divergiam em silêncio, para sempre. E quem fosse
rastrear um defeito de tecido não tinha por onde começar: o único lote que o
sistema guardava era o texto que alguém digitou no corte, que o estoque nunca
soube o que era.

O que os testes cercam:

  · FEFO DE VERDADE: vence primeiro, sai primeiro — e o lote vencido não é
    tocado, mesmo estando cheio;
  · OS DOIS SALDOS ANDAM JUNTOS. É o defeito silencioso: se o lote não desce
    junto com o produto, ninguém percebe até o inventário;
  · O ESTORNO VOLTA PARA O MESMO LOTE. Voltar por FEFO seria errado no sentido
    contrário — jogaria o tecido no rolo que vence primeiro, que raramente é de
    onde ele saiu. Em duas rodadas o total fecharia com os lotes todos trocados,
    com cara de certo;
  · UM CORTE COME VÁRIOS ROLOS, e cada pedaço fica ligado ao seu;
  · O QUE OS LOTES NÃO COBREM ainda é registrado, com aviso. O tecido já foi
    cortado no mundo físico: recusar o lançamento não devolve o rolo, só deixa
    o sistema mais errado do que já estava.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque, LoteProduto, MovimentacaoEstoque
from apps.moda.models import (
    ConsumoLoteCorte, FichaTecnica, ItemPedidoProducao, MaterialFicha,
    OrdemProducao, PedidoProducao, ProdutoModa, RegistroCorte,
)
from apps.moda.services.integracao import IntegracaoService
from apps.produtos.models import Produto, UnidadeMedida

HOJE = timezone.localdate()


class FefoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao FEFO LTDA', nome_fantasia='FEFO',
            cnpj='53345678000191', segmento='moda_confeccao',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='53345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Henry Freitas',
            cpf_cnpj='12345678901', ativo=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='M', descricao='Metro',
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@t.local', nome='Fulano', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.tecido_estoque = Produto.objects.create(
            filial=self.filial, codigo='TEC001', descricao='Malha PV',
            unidade_medida=self.unidade, controla_lote=True,
        )

    # ── Montagem do cenário ──────────────────────────────────────────────

    def _lote(self, numero, quantidade, validade=None, custo='10'):
        return LoteProduto.objects.create(
            filial=self.filial, produto=self.tecido_estoque,
            numero_lote=numero,
            quantidade_inicial=Decimal(quantidade),
            quantidade_atual=Decimal(quantidade),
            custo_unitario=Decimal(custo),
            data_validade=validade,
        )

    def _saldo(self, total):
        Estoque.objects.update_or_create(
            produto=self.tecido_estoque, filial=self.filial,
            defaults={
                'quantidade_atual': Decimal(total),
                'quantidade_reservada': Decimal('0'),
                'quantidade_disponivel': Decimal(total),
            },
        )

    def _corte(self, consumo='30', numero=1):
        produto_moda = ProdutoModa.objects.create(
            filial=self.filial, codigo=f'CAM{numero:03d}', nome='Camisa',
        )
        # A ordem le a ficha pelo PRODUTO (propriedade, nao campo) -- e por
        # isso ela e criada aqui, ligada ao produto, e nao passada a ordem.
        ficha = FichaTecnica.objects.create(
            filial=self.filial, produto=produto_moda,
        )
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha PV', consumo=Decimal('1'),
            produto_estoque=self.tecido_estoque,
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=numero,
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto_moda, descricao='Camisa',
            quantidade=10, valor_unitario=Decimal('45'),
        )
        ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            numero=f'OP-{numero:04d}', ano=2026, sequencial=numero,
            quantidade=10,
        )
        return RegistroCorte.objects.create(
            filial=self.filial, ordem=ordem, numero=numero,
            quantidade=10, consumo_real=Decimal(consumo),
            status=RegistroCorte.Status.CORTADO,
        )

    def _baixar(self, corte):
        return IntegracaoService.baixar_estoque_do_corte(corte, self.usuario)

    def _lote_atual(self, numero):
        return LoteProduto.objects.get(
            numero_lote=numero, filial=self.filial,
        ).quantidade_atual


class OrdemFefoTests(FefoBase):

    def test_o_que_vence_antes_sai_antes(self):
        self._lote('B', 100, HOJE + timedelta(days=90))
        self._lote('A', 100, HOJE + timedelta(days=10))
        self._saldo(200)
        corte = self._corte(consumo='30')

        self._baixar(corte)

        self.assertEqual(self._lote_atual('A'), Decimal('70.000'))
        self.assertEqual(self._lote_atual('B'), Decimal('100.000'))

    def test_lote_sem_validade_fica_por_ultimo(self):
        """
        Sem validade não é "vence já": é indefinido, e consumir primeiro o
        indefinido deixaria vencer o que tem data.
        """
        self._lote('SEM', 100, None)
        self._lote('COM', 100, HOJE + timedelta(days=30))
        self._saldo(200)
        corte = self._corte(consumo='30')

        self._baixar(corte)

        self.assertEqual(self._lote_atual('COM'), Decimal('70.000'))
        self.assertEqual(self._lote_atual('SEM'), Decimal('100.000'))

    def test_lote_vencido_nao_e_tocado(self):
        """Cheio, mas vencido: FEFO pula, e o consumo vai para o vigente."""
        self._lote('VENCIDO', 100, HOJE - timedelta(days=1))
        self._lote('VIGENTE', 100, HOJE + timedelta(days=30))
        self._saldo(200)
        corte = self._corte(consumo='30')

        self._baixar(corte)

        self.assertEqual(self._lote_atual('VENCIDO'), Decimal('100.000'))
        self.assertEqual(self._lote_atual('VIGENTE'), Decimal('70.000'))

    def test_um_corte_pode_comer_varios_rolos(self):
        self._lote('A', 20, HOJE + timedelta(days=10))
        self._lote('B', 100, HOJE + timedelta(days=90))
        self._saldo(120)
        corte = self._corte(consumo='30')

        self._baixar(corte)

        self.assertEqual(self._lote_atual('A'), Decimal('0.000'))
        self.assertEqual(self._lote_atual('B'), Decimal('90.000'))


class OsDoisSaldosAndamJuntosTests(FefoBase):
    """O defeito silencioso: produto desce e lote fica cheio."""

    def test_o_lote_desce_junto_com_o_produto(self):
        self._lote('A', 100, HOJE + timedelta(days=30))
        self._saldo(100)
        corte = self._corte(consumo='30')

        self._baixar(corte)

        saldo = Estoque.objects.get(
            produto=self.tecido_estoque, filial=self.filial,
        ).quantidade_atual
        self.assertEqual(saldo, Decimal('70.000'))
        self.assertEqual(self._lote_atual('A'), Decimal('70.000'))

    def test_a_movimentacao_aponta_o_lote(self):
        """Sem isso o razão não diz de onde saiu, e o rastro morre nele."""
        self._lote('A', 100, HOJE + timedelta(days=30))
        self._saldo(100)
        corte = self._corte(consumo='30')

        self._baixar(corte)

        mov = MovimentacaoEstoque.objects.get(
            produto=self.tecido_estoque,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.PRODUCAO_SAIDA,
        )
        self.assertIsNotNone(mov.lote_id)
        self.assertEqual(mov.lote.numero_lote, 'A')

    def test_a_alocacao_do_corte_fica_gravada(self):
        self._lote('A', 20, HOJE + timedelta(days=10))
        self._lote('B', 100, HOJE + timedelta(days=90))
        self._saldo(120)
        corte = self._corte(consumo='30')

        self._baixar(corte)

        alocado = {
            c.lote.numero_lote: c.quantidade
            for c in ConsumoLoteCorte.objects.filter(corte=corte)
        }
        self.assertEqual(alocado, {
            'A': Decimal('20.0000'), 'B': Decimal('10.0000'),
        })

    def test_o_custo_do_rolo_e_copiado_na_gravacao(self):
        """
        O custo do lote pode ser corrigido depois; o que a peça custou é o do
        dia em que o tecido saiu.
        """
        self._lote('A', 100, HOJE + timedelta(days=30), custo='12.5')
        self._saldo(100)
        corte = self._corte(consumo='30')

        self._baixar(corte)

        self.assertEqual(
            ConsumoLoteCorte.objects.get(corte=corte).custo_unitario,
            Decimal('12.5000'),
        )


class EstornoVoltaAoMesmoLoteTests(FefoBase):

    def test_o_estorno_devolve_ao_rolo_de_onde_saiu(self):
        """
        Voltar por FEFO jogaria o tecido no rolo que vence primeiro. Aqui o
        consumo saiu de B (o único com saldo), e é para B que volta.
        """
        self._lote('A', 5, HOJE + timedelta(days=10))
        self._lote('B', 100, HOJE + timedelta(days=90))
        self._saldo(105)
        corte = self._corte(consumo='30')
        self._baixar(corte)
        self.assertEqual(self._lote_atual('A'), Decimal('0.000'))
        self.assertEqual(self._lote_atual('B'), Decimal('75.000'))

        IntegracaoService.estornar_estoque_do_corte(corte, self.usuario)

        self.assertEqual(self._lote_atual('A'), Decimal('5.000'))
        self.assertEqual(self._lote_atual('B'), Decimal('100.000'))

    def test_baixar_e_estornar_duas_vezes_nao_embaralha_os_lotes(self):
        """
        A prova de que o total fechar não basta. Se o estorno voltasse por
        FEFO, o saldo somado bateria e os rolos estariam trocados.
        """
        self._lote('A', 5, HOJE + timedelta(days=10))
        self._lote('B', 100, HOJE + timedelta(days=90))
        self._saldo(105)
        corte = self._corte(consumo='30')

        for _ in range(2):
            self._baixar(corte)
            IntegracaoService.estornar_estoque_do_corte(corte, self.usuario)

        self.assertEqual(self._lote_atual('A'), Decimal('5.000'))
        self.assertEqual(self._lote_atual('B'), Decimal('100.000'))

    def test_o_estorno_de_um_enfesto_nao_devolve_o_do_outro(self):
        """
        Uma ordem tem vários cortes, e o razão é indexado pelo DOCUMENTO —
        estornar pelo razão devolveria tecido dos outros enfestos.
        """
        self._lote('A', 200, HOJE + timedelta(days=30))
        self._saldo(200)
        primeiro = self._corte(consumo='30', numero=1)
        segundo = RegistroCorte.objects.create(
            filial=self.filial, ordem=primeiro.ordem, numero=2,
            quantidade=10, consumo_real=Decimal('50'),
            status=RegistroCorte.Status.CORTADO,
        )
        self._baixar(primeiro)
        self._baixar(segundo)
        self.assertEqual(self._lote_atual('A'), Decimal('120.000'))

        IntegracaoService.estornar_estoque_do_corte(primeiro, self.usuario)

        # Devolveu só os 30 do primeiro enfesto, não os 80 dos dois.
        self.assertEqual(self._lote_atual('A'), Decimal('150.000'))

    def test_o_estorno_limpa_a_alocacao(self):
        self._lote('A', 100, HOJE + timedelta(days=30))
        self._saldo(100)
        corte = self._corte(consumo='30')
        self._baixar(corte)

        IntegracaoService.estornar_estoque_do_corte(corte, self.usuario)

        self.assertEqual(ConsumoLoteCorte.objects.filter(corte=corte).count(), 0)
        corte.refresh_from_db()
        self.assertIsNone(corte.estoque_baixado_em)

    def test_estorno_de_baixa_antiga_nao_estoura(self):
        """
        Baixa feita antes desta mudança não tem alocação gravada. Devolver o
        total sem lote é o único caminho honesto — o sistema nunca soube de
        que rolo aquilo saiu.
        """
        self._lote('A', 100, HOJE + timedelta(days=30))
        self._saldo(100)
        corte = self._corte(consumo='30')
        self._baixar(corte)
        ConsumoLoteCorte.objects.filter(corte=corte).delete()  # simula o legado

        resultado = IntegracaoService.estornar_estoque_do_corte(corte, self.usuario)

        self.assertEqual(resultado.quantidade, Decimal('30.0000'))
        corte.refresh_from_db()
        self.assertIsNone(corte.estoque_baixado_em)


class SemLoteSuficienteTests(FefoBase):
    """O tecido já foi cortado — recusar não devolve o rolo."""

    def test_o_que_os_lotes_nao_cobrem_ainda_e_registrado(self):
        self._lote('A', 10, HOJE + timedelta(days=30))
        self._saldo(10)
        corte = self._corte(consumo='30')

        resultado = self._baixar(corte)

        self.assertEqual(resultado.quantidade, Decimal('30.0000'))
        self.assertEqual(self._lote_atual('A'), Decimal('0.000'))
        corte.refresh_from_db()
        self.assertIsNotNone(corte.estoque_baixado_em)

    def test_e_avisa_que_ficou_sem_rastro(self):
        """
        A baixa deu certo, mas ficou sem rastro. Silenciar isso deixaria o
        buraco de cadastro invisível justamente para quem podia consertá-lo.
        """
        self._lote('A', 10, HOJE + timedelta(days=30))
        self._saldo(10)
        corte = self._corte(consumo='30')

        resultado = self._baixar(corte)

        self.assertIn('SEM LOTE', resultado.aviso)
        self.assertIn('10', resultado.aviso)

    def test_a_sobra_sem_lote_e_gravada_como_sobra(self):
        """
        O pedaço sem rastro fica registrado, e não omitido: é justamente ele
        que precisa aparecer.
        """
        self._lote('A', 10, HOJE + timedelta(days=30))
        self._saldo(10)
        corte = self._corte(consumo='30')

        self._baixar(corte)

        sem_lote = ConsumoLoteCorte.objects.get(corte=corte, lote__isnull=True)
        self.assertEqual(sem_lote.quantidade, Decimal('20.0000'))

    def test_tecido_sem_lote_nenhum_continua_baixando(self):
        """
        O caminho de antes tem de continuar funcionando: nem toda confecção
        controla lote de tecido, e travar a baixa aí pararia o chão de fábrica
        por causa de um cadastro que ninguém pediu.
        """
        self.tecido_estoque.controla_lote = False
        self.tecido_estoque.save(update_fields=['controla_lote'])
        self._saldo(100)
        corte = self._corte(consumo='30')

        resultado = self._baixar(corte)

        saldo = Estoque.objects.get(
            produto=self.tecido_estoque, filial=self.filial,
        ).quantidade_atual
        self.assertEqual(saldo, Decimal('70.000'))
        self.assertEqual(resultado.quantidade, Decimal('30.0000'))
        self.assertIn('não tem lote vigente', resultado.aviso)

    def test_com_lote_cobrindo_tudo_nao_ha_aviso(self):
        self._lote('A', 100, HOJE + timedelta(days=30))
        self._saldo(100)
        corte = self._corte(consumo='30')

        self.assertEqual(self._baixar(corte).aviso, '')


class TravasQueNaoPodemAfrouxarTests(FefoBase):

    def test_dois_cliques_nao_tiram_o_tecido_duas_vezes(self):
        from apps.core.services.exceptions import DomainError

        self._lote('A', 100, HOJE + timedelta(days=30))
        self._saldo(100)
        corte = self._corte(consumo='30')
        self._baixar(corte)

        with self.assertRaises(DomainError):
            self._baixar(corte)

        self.assertEqual(self._lote_atual('A'), Decimal('70.000'))

    def test_corte_nao_concluido_nao_baixa(self):
        from apps.core.services.exceptions import DomainError

        self._lote('A', 100, HOJE + timedelta(days=30))
        self._saldo(100)
        corte = self._corte(consumo='30')
        corte.status = RegistroCorte.Status.PLANEJADO
        corte.save(update_fields=['status'])

        with self.assertRaises(DomainError):
            self._baixar(corte)

    def test_selecionar_lotes_continua_recusando_por_padrao(self):
        """
        O parcial é só para o consumo que JÁ aconteceu. Quem VAI tirar material
        — venda, separação — tem de continuar batendo na recusa.
        """
        from apps.core.services.exceptions import EstoqueInsuficienteError
        from apps.estoque.services.movimentacao_service import MovimentacaoService

        self._lote('A', 10, HOJE + timedelta(days=30))

        with self.assertRaises(EstoqueInsuficienteError):
            MovimentacaoService.selecionar_lotes_fifo(
                self.tecido_estoque.pk, self.filial.pk, Decimal('30'),
            )
