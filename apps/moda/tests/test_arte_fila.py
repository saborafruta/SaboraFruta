"""
A fila da arte — o que falta desenhar, o que espera aceite do layout.

O RISCO DESTA TELA É DISCORDAR DA VALIDAÇÃO DE PRODUÇÃO. Ela responde "a
arte deste pedido existe?", e a mesma pergunta é feita por
`ValidacaoProducao._arte` na hora de emitir a ordem. Se as duas lerem a
regra de jeitos diferentes, a fila diz "tudo certo" e o botão de produzir
trava o pedido — e ninguém sabe em qual acreditar. Por isso o teste mais
importante daqui confronta as duas.

Os outros jeitos de errar:

  · peça LISA cobrada como se precisasse de arte. A maior parte do que uma
    confecção faz não leva estampa nenhuma, e a fila encheria de pedido que
    não tem nada a resolver;
  · orçamento sem arte em jogo enchendo a tela — proposta que nem arte terá
    não é assunto desta fila;
  · o balde errado. São cinco situações com providências diferentes, e o
    pedido em cada uma espera uma pessoa diferente: o designer, o vendedor,
    o cliente.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.moda.models import (
    AprovacaoPedido, ItemPedidoProducao, PedidoProducao, Personalizacao,
    ProdutoModa,
)
from apps.moda.services.arte import FilaArteService


class ArteBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Arte LTDA', nome_fantasia='Arte',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Arte LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Escola Sao Jose',
            cpf_cnpj='12345678901',
        )

    def setUp(self):
        self.produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        self._n = 0

    # ── Montagem ─────────────────────────────────────────────────────────

    def _pedido(self, status=PedidoProducao.Status.CONFIRMADO,
                dias_atras=0, cliente=None):
        self._n += 1
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=cliente or self.cliente,
            numero=self._n, status=status,
        )
        if dias_atras:
            # `data_pedido` costuma ter default de hoje; a fila mede a idade
            # a partir dela, então o teste precisa envelhecer o pedido.
            PedidoProducao.objects.filter(pk=pedido.pk).update(
                data_pedido=timezone.localdate() - timedelta(days=dias_atras),
            )
            pedido.refresh_from_db()
        return pedido

    def _item(self, pedido, quantidade=10):
        return ItemPedidoProducao.objects.create(
            pedido=pedido, produto=self.produto, descricao='Camisa',
            quantidade=quantidade,
        )

    def _personalizacao(self, item, com_arquivo=False):
        """
        Declara personalização no item. Com arquivo, a arte existe; sem
        arquivo, o item DECLARA arte e não tem o que aplicar — que é
        exatamente o que trava a fábrica.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        return Personalizacao.objects.create(
            item=item,
            tipo=Personalizacao.Tipo.ESCUDO,
            local='Peito',
            arquivo=(
                SimpleUploadedFile('escudo.png', b'\x89PNG\r\n\x1a\n')
                if com_arquivo else None
            ),
        )

    def _aprovacao(self, pedido, liberada=False, resposta=None):
        aprovacao = AprovacaoPedido.objects.create(pedido=pedido)
        if liberada or resposta:
            aprovacao.liberar(usuario=None)
        if resposta:
            aprovacao.responder(resposta=resposta, nome='Diego Macedo')
        return aprovacao

    def _fila(self, busca=''):
        return FilaArteService.montar(self.filial, busca=busca)

    def _onde(self, pedido):
        """Em que balde o pedido caiu — ou None se ficou de fora."""
        fila = self._fila()
        for nome in ('falta', 'pronta', 'enviada', 'ajuste', 'aceita'):
            if any(l.pedido.pk == pedido.pk for l in fila[nome]):
                return nome
        return None


class RegraDaArteTests(ArteBase):
    """O que conta como "tem arte" — e tem de contar igual em toda parte."""

    def test_item_que_declara_personalizacao_sem_arquivo_trava(self):
        pedido = self._pedido()
        self._personalizacao(self._item(pedido), com_arquivo=False)

        self.assertEqual(self._onde(pedido), 'falta')
        self.assertEqual(
            FilaArteService.itens_sem_arte(pedido), ['Camisa'],
        )

    def test_peca_lisa_nao_precisa_de_arte(self):
        """
        A maior parte do que uma confecção faz não leva estampa. Cobrar arte
        de peça lisa encheria a fila de pedido sem nada a resolver.
        """
        pedido = self._pedido()
        self._item(pedido)

        self.assertEqual(FilaArteService.itens_sem_arte(pedido), [])
        self.assertNotEqual(self._onde(pedido), 'falta')

    def test_com_arquivo_a_arte_existe(self):
        pedido = self._pedido()
        self._personalizacao(self._item(pedido), com_arquivo=True)

        self.assertEqual(FilaArteService.itens_sem_arte(pedido), [])
        self.assertEqual(len(FilaArteService.artes_do_pedido(pedido)), 1)

    def test_a_fila_e_a_validacao_de_producao_concordam(self):
        """
        O teste que mais importa: se as duas lerem a regra diferente, a fila
        diz "tudo certo" e o botão de produzir trava o pedido — e ninguém
        sabe em qual acreditar.
        """
        from apps.moda.services.validacao import ValidacaoProducao

        travado = self._pedido()
        self._personalizacao(self._item(travado), com_arquivo=False)
        liberado = self._pedido()
        self._personalizacao(self._item(liberado), com_arquivo=True)

        def validacao_reclama(pedido):
            # Pela CHAVE e nao pelo rotulo: o rotulo e' texto de tela, e
            # casar por ele faria o teste quebrar numa troca de redacao.
            return any(
                c.chave == 'arte' and c.bloqueia
                for c in ValidacaoProducao.checar(pedido)
            )

        self.assertTrue(bool(FilaArteService.itens_sem_arte(travado)))
        self.assertTrue(validacao_reclama(travado))

        self.assertFalse(bool(FilaArteService.itens_sem_arte(liberado)))
        self.assertFalse(validacao_reclama(liberado))


class BaldesTests(ArteBase):
    """Cinco situações, e em cada uma o pedido espera uma pessoa diferente."""

    def test_sem_arte_espera_o_designer(self):
        pedido = self._pedido()
        self._personalizacao(self._item(pedido), com_arquivo=False)

        self.assertEqual(self._onde(pedido), 'falta')

    def test_com_arte_e_sem_liberar_espera_o_vendedor(self):
        """A arte está pronta e ninguém mandou para o cliente."""
        pedido = self._pedido()
        self._personalizacao(self._item(pedido), com_arquivo=True)

        self.assertEqual(self._onde(pedido), 'pronta')

    def test_liberada_e_sem_resposta_espera_o_cliente(self):
        pedido = self._pedido()
        self._personalizacao(self._item(pedido), com_arquivo=True)
        self._aprovacao(pedido, liberada=True)

        self.assertEqual(self._onde(pedido), 'enviada')

    def test_ajuste_pedido_pelo_cliente_volta_para_o_designer(self):
        pedido = self._pedido()
        self._personalizacao(self._item(pedido), com_arquivo=True)
        self._aprovacao(pedido, resposta=AprovacaoPedido.Resposta.AJUSTE)

        self.assertEqual(self._onde(pedido), 'ajuste')

    def test_aprovada_sai_da_fila_de_trabalho(self):
        pedido = self._pedido()
        self._personalizacao(self._item(pedido), com_arquivo=True)
        self._aprovacao(pedido, resposta=AprovacaoPedido.Resposta.APROVADO)

        self.assertEqual(self._onde(pedido), 'aceita')

    def test_falta_de_arte_vence_o_resto(self):
        """
        Um pedido com arte aprovada num item e faltando noutro ainda está
        travado: a fábrica não corta o que não tem layout.
        """
        pedido = self._pedido()
        self._personalizacao(self._item(pedido), com_arquivo=True)
        self._personalizacao(self._item(pedido), com_arquivo=False)
        self._aprovacao(pedido, resposta=AprovacaoPedido.Resposta.APROVADO)

        self.assertEqual(self._onde(pedido), 'falta')


class RecorteTests(ArteBase):
    """Quem entra na fila."""

    def test_orcamento_com_arte_em_jogo_entra(self):
        """
        Na confecção o layout costuma ser desenhado ANTES de o pedido
        fechar: é o que convence o cliente.
        """
        pedido = self._pedido(status=PedidoProducao.Status.ORCAMENTO)
        self._personalizacao(self._item(pedido), com_arquivo=False)

        self.assertEqual(self._onde(pedido), 'falta')

    def test_orcamento_sem_arte_em_jogo_fica_de_fora(self):
        """Proposta que nem arte terá não é assunto desta tela."""
        pedido = self._pedido(status=PedidoProducao.Status.ORCAMENTO)
        self._item(pedido)

        self.assertIsNone(self._onde(pedido))

    def test_entregue_e_cancelado_saem_da_fila(self):
        for status in (PedidoProducao.Status.ENTREGUE,
                       PedidoProducao.Status.CANCELADO):
            pedido = self._pedido(status=status)
            self._personalizacao(self._item(pedido), com_arquivo=False)

            self.assertIsNone(self._onde(pedido))

    def test_a_busca_filtra_por_cliente_e_numero(self):
        outro = Cliente.objects.create(
            filial=self.filial, razao_social='Time do Bairro',
            cpf_cnpj='22222222222',
        )
        escola = self._pedido()
        time = self._pedido(cliente=outro)
        self._personalizacao(self._item(escola), com_arquivo=False)
        self._personalizacao(self._item(time), com_arquivo=False)

        por_cliente = self._fila(busca='Escola')['falta']
        por_numero = self._fila(busca=str(time.numero))['falta']

        self.assertEqual([l.pedido.pk for l in por_cliente], [escola.pk])
        self.assertEqual([l.pedido.pk for l in por_numero], [time.pk])


class OrdemEResumoTests(ArteBase):

    def test_o_mais_parado_vem_primeiro(self):
        """
        A fila é de trabalho: quem está esperando há mais tempo precisa
        aparecer antes, senão some no meio da lista.
        """
        novo = self._pedido(dias_atras=1)
        antigo = self._pedido(dias_atras=40)
        self._personalizacao(self._item(novo), com_arquivo=False)
        self._personalizacao(self._item(antigo), com_arquivo=False)

        falta = self._fila()['falta']

        self.assertEqual([l.pedido.pk for l in falta], [antigo.pk, novo.pk])
        self.assertEqual(falta[0].dias_parado, 40)

    def test_o_resumo_conta_as_pecas_travadas(self):
        """
        Pedido travado por arte é peça que a fábrica não pode cortar — e o
        número de PEÇAS é o que mostra o tamanho do problema.
        """
        pedido = self._pedido()
        self._personalizacao(self._item(pedido, quantidade=250), com_arquivo=False)

        resumo = self._fila()['resumo']

        self.assertEqual(resumo['falta'], 1)
        self.assertEqual(resumo['pecas_travadas'], 250)

    def test_fila_vazia_nao_estoura(self):
        fila = self._fila()

        self.assertEqual(fila['falta'], [])
        self.assertEqual(fila['resumo']['pecas_travadas'], 0)


class TelaArteTests(TestCase):
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
        resposta = self.client.get(reverse('moda:arte-fila'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Nenhum pedido travado por falta de arte')

    def test_a_tela_mostra_o_pedido_travado(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Escola Sao Jose',
            cpf_cnpj='12345678901',
        )
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=cliente, numero=1,
            status=PedidoProducao.Status.CONFIRMADO,
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa', quantidade=120,
        )
        Personalizacao.objects.create(
            item=item, tipo=Personalizacao.Tipo.ESCUDO, local='Peito',
        )
        # Um segundo pedido, com arte, para a seção de "pronta" também sair.
        pedido2 = PedidoProducao.objects.create(
            filial=self.filial, cliente=cliente, numero=2,
            status=PedidoProducao.Status.CONFIRMADO,
        )
        item2 = ItemPedidoProducao.objects.create(
            pedido=pedido2, produto=produto, descricao='Camisa', quantidade=30,
        )
        Personalizacao.objects.create(
            item=item2, tipo=Personalizacao.Tipo.ESCUDO, local='Costas',
            arquivo=SimpleUploadedFile('escudo.png', b'\x89PNG\r\n\x1a\n'),
        )

        resposta = self.client.get(reverse('moda:arte-fila'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Escola Sao Jose')
        self.assertContains(resposta, 'Camisa')

    def test_a_rota_do_menu_cai_na_tela(self):
        from apps.moda.views_aprovacao import FilaArteView

        for url in (
            reverse('moda:arte-fila'),
            reverse('moda:item', args=['comercial', 'aprovacao-arte']),
        ):
            self.assertIs(resolve(url).func.view_class, FilaArteView)

    def test_a_aprovacao_de_pedido_continua_sendo_outra_tela(self):
        """
        Uma pergunta quem espera decisão comercial; a outra, se o layout
        existe e foi aceito. Confundir os endereços faria uma sumir.
        """
        from apps.moda.views_aprovacao import FilaAprovacaoView

        self.assertIs(
            resolve(reverse('moda:aprovacao-fila')).func.view_class,
            FilaAprovacaoView,
        )
