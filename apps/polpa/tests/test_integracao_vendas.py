"""
Integração com vendas: pedido → estoque → necessidade → sugestão de OP.

ESTE ARQUIVO EXISTE PARA PROVAR, e não para construir. `PlanejamentoService`
já fazia a conta antes desta sessão, e a tela já traz o botão "Abrir OP"
preenchido com a necessidade. O que faltava era alguém percorrer o caminho da
especificação de ponta a ponta e mostrar que o número que sai é o esperado —
serviço que ninguém exercita é como aparecem os três blocos mortos que já
apareceram neste repositório.

O CENÁRIO É O DA ESPECIFICAÇÃO, com os nomes dela: 500 caixas pedidas, 300 em
estoque, 200 de necessidade, e a OP de 200.

A CONTA DO SISTEMA TEM UM TERMO A MAIS que a especificação não pede, e ele é o
que evita produzir duas vezes:

    necessidade = pedidos + mínimo + previsão − estoque − JÁ EM PRODUÇÃO

Sem o último, a sugestão manda fazer de novo o que já está na linha. Os testes
cercam isso também, porque é a diferença entre uma sugestão que ajuda e uma
que enche a câmara.

E O ESTOQUE RESERVADO já entra: `Estoque.quantidade_disponivel` é o físico
menos o reservado. Trezentas caixas separadas para outro cliente não contam
como cobertura — e não contar seria prometer duas vezes a mesma caixa.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque
from apps.polpa.models import EtapaReceita, FichaProduto, OrdemPolpa
from apps.polpa.services import CatalogoService, OrdemPolpaService, ReceitaService
from apps.polpa.services.planejamento import PlanejamentoService
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial
from apps.vendas.models import ItemPedidoVenda, PedidoVenda

T = FichaProduto.Tipo
S = OrdemPolpa.Situacao
ZERO = Decimal('0')


class VendasBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Vendas LTDA', nome_fantasia='Vendas',
            cnpj='83345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='83345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='CX', descricao='Caixa',
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@vendas.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Distribuidora Norte',
            cpf_cnpj='12345678901', ativo=True,
        )

    def setUp(self):
        self.acerola = self._item(T.POLPA, 'Polpa de acerola', validade_dias=180)
        self.fruta = self._item(T.FRUTA, 'Acerola in natura')
        self.receita = self._receita()

    # ── Montagem ─────────────────────────────────────────────────────────

    def _item(self, tipo, descricao, **extras):
        dados = {
            'tipo': tipo, 'descricao': descricao, 'codigo': descricao[:10],
            'unidade_medida': self.unidade,
        }
        dados.update(extras)
        return CatalogoService.salvar(self.filial, dados).produto

    def _receita(self):
        receita = ReceitaService.criar(self.filial, self.acerola, {
            'descricao': 'Polpa de acerola', 'versao': '1.0',
            'quantidade_produzida': Decimal('100'),
            'rendimento_esperado': Decimal('60'),
        })
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.fruta,
            quantidade=Decimal('150'), perda_prevista=ZERO,
        )
        EtapaReceita.objects.create(receita=receita, ordem=1, nome='Despolpa')
        ReceitaService.ativar(receita)
        return receita

    def _estoque(self, quantidade, reservado='0'):
        disponivel = Decimal(quantidade) - Decimal(reservado)
        Estoque.objects.update_or_create(
            produto=self.acerola, filial=self.filial,
            defaults={
                'quantidade_atual': Decimal(quantidade),
                'quantidade_reservada': Decimal(reservado),
                'quantidade_disponivel': disponivel,
            },
        )

    def _pedido(self, quantidade, atendida='0', status=None):
        pedido = PedidoVenda.objects.create(
            filial=self.filial, cliente=self.cliente, usuario=self.usuario,
            data_emissao=timezone.localdate(),
            status=status or PedidoVenda.Status.APROVADO,
        )
        ItemPedidoVenda.objects.create(
            pedido=pedido, produto=self.acerola,
            quantidade=Decimal(quantidade),
            quantidade_atendida=Decimal(atendida),
            valor_unitario=Decimal('10'),
            valor_bruto=Decimal(quantidade) * 10,
            valor_total=Decimal(quantidade) * 10,
        )
        return pedido

    def _linha(self, horizonte=30):
        sugestoes = PlanejamentoService.sugestoes(self.filial, horizonte)
        return next(l for l in sugestoes if l['produto'].pk == self.acerola.pk)


class OExemploDaEspecificacaoTests(VendasBase):

    def test_500_pedidas_300_em_estoque_dao_200_de_necessidade(self):
        """O caminho inteiro da especificação, com os números dela."""
        self._pedido('500')
        self._estoque('300')

        linha = self._linha()

        self.assertEqual(linha['pedidos'], Decimal('500'))
        self.assertEqual(linha['estoque'], Decimal('300'))
        self.assertEqual(linha['necessidade'], Decimal('200'))

    def test_a_sugestao_vem_com_a_receita_para_abrir_a_op(self):
        """
        Sem receita ativa não há o que abrir — e a tela diz isso em vez de
        oferecer um botão que falharia.
        """
        self._pedido('500')
        self._estoque('300')

        self.assertEqual(self._linha()['receita'], self.receita)

    def test_a_op_sai_com_a_quantidade_sugerida(self):
        self._pedido('500')
        self._estoque('300')
        necessidade = self._linha()['necessidade']

        op = OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': necessidade}, self.usuario,
        )

        self.assertEqual(op.quantidade_planejada, Decimal('200.000'))
        self.assertEqual(op.situacao, S.PLANEJADA)

    def test_estoque_cobrindo_tudo_nao_sugere_nada(self):
        """
        Ver que o produto está coberto é informação — a linha continua na
        lista com necessidade zero, em vez de sumir e alguém perguntar se foi
        esquecida.
        """
        self._pedido('500')
        self._estoque('800')

        self.assertEqual(self._linha()['necessidade'], ZERO)


class OQueJaSaiuNaoContaTests(VendasBase):

    def test_a_parte_ja_atendida_do_pedido_sai_da_conta(self):
        """
        O que foi faturado já saiu do estoque; contá-lo de novo mandaria
        produzir para uma venda que já foi entregue.
        """
        self._pedido('500', atendida='300')
        self._estoque('0')

        self.assertEqual(self._linha()['necessidade'], Decimal('200'))

    def test_pedido_em_rascunho_nao_gera_producao(self):
        """
        Orçamento não é compromisso. Produzir por ele é encher a câmara de
        item que o cliente ainda pode não pedir.
        """
        self._pedido('500', status=PedidoVenda.Status.RASCUNHO)
        self._estoque('0')

        self.assertEqual(self._linha()['necessidade'], ZERO)


class NaoProduzirDuasVezesTests(VendasBase):
    """O termo que a especificação não pede e é o que evita o dobro."""

    def _ordem_aberta(self, quantidade):
        return OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal(quantidade)}, self.usuario,
        )

    def test_ordem_ja_aberta_desconta_da_necessidade(self):
        """
        Sem isto a sugestão manda produzir de novo o que já está na linha — e
        é assim que se produz o dobro do que se vende.
        """
        self._pedido('500')
        self._estoque('300')
        self._ordem_aberta('200')

        linha = self._linha()

        self.assertEqual(linha['em_producao'], Decimal('200.000'))
        self.assertEqual(linha['necessidade'], ZERO)

    def test_ordem_parcial_desconta_so_o_que_cobre(self):
        self._pedido('500')
        self._estoque('300')
        self._ordem_aberta('50')

        self.assertEqual(self._linha()['necessidade'], Decimal('150.000'))

    def test_ordem_cancelada_volta_a_gerar_necessidade(self):
        """
        A ordem cancelada não vai produzir nada — deixá-la descontando faria
        a fábrica ficar sem o produto achando que ele estava a caminho.
        """
        self._pedido('500')
        self._estoque('300')
        op = self._ordem_aberta('200')

        OrdemPolpaService.mover(
            op, S.CANCELADA, self.usuario, {'motivo': 'Faltou fruta'},
        )

        self.assertEqual(self._linha()['necessidade'], Decimal('200'))


class EstoqueReservadoTests(VendasBase):
    """O passo do meio da especificação: disponível, não físico."""

    def test_o_reservado_nao_conta_como_cobertura(self):
        """
        Trezentas caixas separadas para outro cliente não cobrem este pedido.
        Contá-las seria prometer duas vezes a mesma caixa.
        """
        self._pedido('500')
        self._estoque('300', reservado='300')

        linha = self._linha()

        self.assertEqual(linha['estoque'], ZERO)
        self.assertEqual(linha['necessidade'], Decimal('500'))

    def test_o_reservado_aparece_ao_lado_do_disponivel(self):
        """
        Ele não entra na conta — o disponível já é o físico menos ele. Serve
        para explicar: "estoque 0" com 300 caixas no galpão parece defeito do
        sistema até alguém ver que as 300 são de outro cliente.
        """
        self._pedido('500')
        self._estoque('300', reservado='300')

        self.assertEqual(self._linha()['reservado'], Decimal('300.000'))

    def test_sem_reserva_o_campo_vem_zerado(self):
        self._pedido('500')
        self._estoque('300')

        self.assertEqual(self._linha()['reservado'], ZERO)

    def test_reserva_parcial_cobre_so_o_livre(self):
        self._pedido('500')
        self._estoque('300', reservado='100')

        self.assertEqual(self._linha()['necessidade'], Decimal('300'))


class EstoqueMinimoTests(VendasBase):

    def test_o_minimo_entra_na_necessidade(self):
        """
        Produzir só o que foi pedido deixa a câmara zerada no dia seguinte —
        o mínimo é o colchão que a fábrica decidiu manter.
        """
        self.acerola.estoque_minimo = Decimal('100')
        self.acerola.save(update_fields=['estoque_minimo'])
        self._pedido('500')
        self._estoque('300')

        self.assertEqual(self._linha()['necessidade'], Decimal('300'))


class TelaTests(VendasBase):

    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def _abrir(self):
        from django.urls import reverse
        return self.client.get(reverse('polpa:planejamento'))

    def test_a_tela_mostra_a_necessidade_e_o_botao(self):
        self._pedido('500')
        self._estoque('300')

        resposta = self._abrir()

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Polpa de acerola')
        self.assertContains(resposta, 'Abrir OP')

    def test_o_botao_vem_preenchido_com_a_necessidade(self):
        """
        Digitar de novo o número que a tela acabou de calcular é onde entra o
        erro de digitação.
        """
        self._pedido('500')
        self._estoque('300')

        self.assertContains(self._abrir(), 'value="200"')

    def test_a_tela_explica_o_disponivel_baixo(self):
        self._pedido('500')
        self._estoque('300', reservado='300')

        self.assertContains(self._abrir(), 'reservado')

    def test_sem_receita_ativa_a_tela_explica_em_vez_de_oferecer(self):
        outro = self._item(T.ACAI, 'Polpa de açaí')

        resposta = self._abrir()

        self.assertContains(resposta, 'sem receita ativa')
        self.assertEqual(outro.descricao, 'Polpa de açaí')

    def test_gerar_pela_tela_cria_a_ordem_planejada(self):
        from django.urls import reverse

        self._pedido('500')
        self._estoque('300')

        self.client.post(reverse('polpa:planejamento-gerar'), {
            'receita': self.receita.pk, 'quantidade': '200', 'horizonte': 30,
        })

        op = OrdemPolpa.all_objects.get()
        self.assertEqual(op.quantidade_planejada, Decimal('200.000'))
        self.assertEqual(op.situacao, S.PLANEJADA)
