"""
A mercadoria de um pedido sai do estoque uma vez.

O DEFEITO QUE ESTES TESTES TRANCAM. Um pedido de venda tinha duas portas
para o mesmo movimento físico: o FATURAMENTO baixava o estoque, e a VIAGEM
baixava de novo ao fechar a carga. Nenhuma das duas está errada sozinha —
faturar antes de carregar é comum (pedido faturado é carregável de
propósito), e carregar antes de faturar também, porque o caminhão sai de
madrugada e a nota sai depois. Juntas, elas tiravam a mesma caixa duas
vezes: 1.000 viravam 800 com 100 vendidas.

A REGRA É POR QUANTIDADE, NÃO POR PORTA: cada uma pergunta quanto daquele
pedido já saiu e baixa só a diferença. Quem chega primeiro baixa; quem chega
depois não repete — e a ordem entre as duas deixa de importar, que é a única
forma de isso não voltar.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import Viagem
from apps.logistica.services.saida_unica import SaidaUnicaService
from apps.logistica.services.vendas_para_carga import VendasParaCargaService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial
from apps.vendas.models.pedido import ItemPedidoVenda, PedidoVenda
from apps.vendas.services.venda_service import VendaService

ZERO = Decimal('0')


class BaixaUnicaBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Baixa Unica LTDA', nome_fantasia='Baixa',
            cnpj='11345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='11345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='CX', descricao='Caixa',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='doca@baixa.local', nome='Doca', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado Central',
            cpf_cnpj='12345678901', uf='RN',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade,
            descricao='Caixa de polpa', codigo='CX1', ncm='20079900',
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)
        natureza = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='venda', descricao='Venda',
            especie=NaturezaOperacao.Especie.VENDA,
        )
        RegraNaturezaOperacao.objects.create(natureza=natureza, cfop='5102')

    def setUp(self):
        self.estoque, _ = Estoque.objects.get_or_create(
            produto=self.produto, filial=self.filial,
        )
        self.estoque.quantidade_atual = Decimal('1000')
        self.estoque.quantidade_reservada = Decimal('0')
        self.estoque.atualizar_disponivel()
        self.estoque.save()

    # ── Fixtures ─────────────────────────────────────────────────────────

    def _pedido(self, quantidade='100'):
        pedido = PedidoVenda.objects.create(
            filial=self.filial,
            numero_pedido=f'PV{PedidoVenda.objects.count() + 1}',
            cliente=self.cliente, usuario=self.usuario,
            status=PedidoVenda.Status.CONFIRMADO, data_emissao=timezone.now(),
        )
        ItemPedidoVenda.objects.create(
            pedido=pedido, produto=self.produto, quantidade=Decimal(quantidade),
            valor_unitario=Decimal('10'),
            valor_bruto=Decimal(quantidade) * 10,
            valor_total=Decimal(quantidade) * 10,
        )
        return pedido

    def _reservar(self, quantidade='100'):
        """O faturamento libera reserva — sem ela ele nem chega no estoque."""
        self.estoque.refresh_from_db()
        self.estoque.quantidade_reservada = Decimal(quantidade)
        self.estoque.atualizar_disponivel()
        self.estoque.save()

    def _viagem(self, pedidos, numero=None):
        viagem = Viagem.objects.create(
            filial=self.filial,
            numero=numero or (Viagem.objects.count() + 1),
            motorista_nome='Seu Zé', veiculo_placa='ABC1D23',
            responsavel=self.usuario,
        )
        VendasParaCargaService.adicionar_vendas(viagem, pedidos)
        return viagem

    def _saldo(self) -> Decimal:
        self.estoque.refresh_from_db()
        return self.estoque.quantidade_atual

    def _saidas(self) -> int:
        return MovimentacaoEstoque.objects.filter(
            produto=self.produto,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
        ).count()


class FaturarAntesTests(BaixaUnicaBase):
    """Faturado e depois carregado — o caso que o sistema oferece na tela."""

    def test_a_carga_nao_baixa_o_que_o_faturamento_ja_baixou(self):
        pedido = self._pedido()
        self._reservar()
        VendaService.faturar_pedido(pedido, self.usuario)
        self.assertEqual(self._saldo(), Decimal('900.000'))

        viagem = self._viagem([pedido])
        ViagemService.fechar_carga(viagem, self.usuario)

        # 100 caixas, uma saída só.
        self.assertEqual(self._saldo(), Decimal('900.000'))
        self.assertEqual(self._saidas(), 1)


class CarregarAntesTests(BaixaUnicaBase):
    """Carregado de madrugada, faturado depois — a ordem inversa."""

    def test_o_faturamento_nao_baixa_o_que_a_carga_ja_levou(self):
        pedido = self._pedido()
        viagem = self._viagem([pedido])
        ViagemService.fechar_carga(viagem, self.usuario)
        self.assertEqual(self._saldo(), Decimal('900.000'))

        self._reservar()
        VendaService.faturar_pedido(pedido, self.usuario)

        self.assertEqual(self._saldo(), Decimal('900.000'))
        self.assertEqual(self._saidas(), 1)

    def test_a_reserva_continua_sendo_liberada(self):
        """
        A reserva é do pedido, não do movimento: deixá-la presa faria o
        produto parecer indisponível para sempre.
        """
        pedido = self._pedido()
        viagem = self._viagem([pedido])
        ViagemService.fechar_carga(viagem, self.usuario)
        self._reservar()

        VendaService.faturar_pedido(pedido, self.usuario)

        self.estoque.refresh_from_db()
        self.assertEqual(self.estoque.quantidade_reservada, ZERO)


class ParcialTests(BaixaUnicaBase):
    """Metade carregada, metade faturada."""

    def test_a_carga_parcial_baixa_so_o_que_falta_no_faturamento(self):
        pedido = self._pedido(quantidade='100')
        viagem = self._viagem([pedido])
        # A carga levou 60 das 100 do pedido.
        item = viagem.itens.first()
        item.quantidade = Decimal('60')
        item.save(update_fields=['quantidade'])
        ViagemService.fechar_carga(viagem, self.usuario)
        self.assertEqual(self._saldo(), Decimal('940.000'))

        self._reservar()
        VendaService.faturar_pedido(pedido, self.usuario)

        # 60 + 40 = 100, e não 160.
        self.assertEqual(self._saldo(), Decimal('900.000'))


class ContagemTests(BaixaUnicaBase):
    """O que cada porta enxerga."""

    def test_viagem_em_planejamento_nao_conta_como_embarcada(self):
        """
        Descontar carga que ainda pode ser desmontada deixaria o faturamento
        sem baixar coisa nenhuma.
        """
        pedido = self._pedido()
        self._viagem([pedido])

        self.assertEqual(
            SaidaUnicaService.embarcado_em_viagem(pedido.pk, self.produto.pk),
            ZERO,
        )

    def test_viagem_cancelada_nao_conta(self):
        pedido = self._pedido()
        viagem = self._viagem([pedido])
        ViagemService.fechar_carga(viagem, self.usuario)
        viagem.status = Viagem.Status.CANCELADA
        viagem.save(update_fields=['status'])

        self.assertEqual(
            SaidaUnicaService.embarcado_em_viagem(pedido.pk, self.produto.pk),
            ZERO,
        )

    def test_a_viagem_que_fecha_agora_nao_desconta_a_si_mesma(self):
        """Sem isso, a carga nunca baixaria o próprio estoque."""
        pedido = self._pedido()
        viagem = self._viagem([pedido])
        viagem.status = Viagem.Status.EM_PREPARACAO
        viagem.save(update_fields=['status'])

        a_baixar = SaidaUnicaService.a_baixar(
            self.filial.pk, pedido.pk, self.produto.pk, Decimal('100'),
            ignorar_viagem=viagem,
        )

        self.assertEqual(a_baixar, Decimal('100'))

    def test_nunca_devolve_negativo(self):
        """
        Negativo faria a porta seguinte DEVOLVER mercadoria ao estoque — um
        jeito novo de errar o mesmo número.
        """
        pedido = self._pedido()
        self._reservar()
        VendaService.faturar_pedido(pedido, self.usuario)

        a_baixar = SaidaUnicaService.a_baixar(
            self.filial.pk, pedido.pk, self.produto.pk, Decimal('40'),
        )

        self.assertEqual(a_baixar, ZERO)

    def test_venda_fora_nao_passa_por_esta_regra(self):
        """
        A remessa sem comprador não tem pedido: ela baixa sempre, e é o saldo
        da viagem que responde por ela depois.
        """
        natureza = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='remessa', descricao='Remessa venda fora',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            # Sem comprador nao ha' destinatario -- e' o que define a especie.
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(natureza=natureza, cfop='5904')
        viagem = Viagem.objects.create(
            filial=self.filial, numero=99, motorista_nome='Seu Zé',
            veiculo_placa='ABC1D23', responsavel=self.usuario,
            vendedor=self.usuario,
        )
        ViagemService.adicionar_item(viagem, {
            'natureza': natureza, 'produto': self.produto,
            'quantidade': Decimal('50'), 'valor_unitario': Decimal('10'),
        })

        ViagemService.fechar_carga(viagem, self.usuario)

        self.assertEqual(self._saldo(), Decimal('950.000'))
        self.assertEqual(viagem.saldos.first().quantidade_remetida, Decimal('50.000'))
