"""
A separação vista do chão da câmara.

O QUE ESTES TESTES CERCAM:

  · NÃO EXISTE UMA SEGUNDA SEPARAÇÃO. O documento é o do ERP
    (`SeparacaoPedido`), e a tela do vertical só acrescenta o que falta a
    quem entra na câmara: ONDE o lote está e QUANTOS DIAS ele tem. Duas
    separações do mesmo pedido obrigariam o faturamento a escolher uma;

  · A ORDEM FEFO NÃO É REESCRITA AQUI. Ela mora em
    `MovimentacaoService.selecionar_lotes_fifo`, e a sugestão da tela
    pergunta a ela — duas ordenações da mesma regra consumiriam pelo lote
    errado no dia em que uma mudasse;

  · LOTE VENCIDO OU BLOQUEADO NÃO ENTRA, e a tela diz por quê. É assim que
    ele deixa de sair por engano;

  · FALTA DE SALDO APARECE, não trava a página. Quem está na doca precisa
    ler "faltam 120 kg" antes do caminhão chegar.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.estoque.models import Estoque, LoteProduto
from apps.polpa.models import Camara, FichaProduto, LoteArmazenado
from apps.polpa.services import CatalogoService
from apps.polpa.services.separacao import SeparacaoPolpaService
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial
from apps.vendas.models import ItemPedidoVenda, PedidoVenda

T = FichaProduto.Tipo
ST = PedidoVenda.Status


class SeparacaoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Expedicao LTDA', nome_fantasia='Expedicao',
            cnpj='53345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='53345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='KG', descricao='Quilograma',
            tipo=UnidadeMedida.Tipo.PESO,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado Central',
            cpf_cnpj='12345678901',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='doca@expedicao.local', nome='Doca', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.polpa = CatalogoService.salvar(self.filial, {
            'tipo': T.POLPA, 'descricao': 'Polpa de manga 1 kg', 'codigo': 'PM1',
            'unidade_medida': self.unidade, 'validade_dias': 180,
            'peso_liquido': Decimal('1'),
        }).produto

    # ── Fixtures ─────────────────────────────────────────────────────────

    def _lote(self, quantidade, dias_validade=90, status=None, produto=None):
        produto = produto or self.polpa
        estoque, _ = Estoque.objects.get_or_create(
            produto=produto, filial=self.filial,
        )
        estoque.quantidade_atual = (estoque.quantidade_atual or Decimal('0')) + quantidade
        estoque.quantidade_reservada = Decimal('0')
        estoque.atualizar_disponivel()
        estoque.save()
        return LoteProduto.objects.create(
            filial=self.filial, produto=produto,
            numero_lote=f'L{LoteProduto.objects.count() + 1}',
            quantidade_inicial=quantidade, quantidade_atual=quantidade,
            data_validade=timezone.localdate() + timedelta(days=dias_validade),
            status=status or LoteProduto.Status.ATIVO,
        )

    def _guardar(self, lote, nome='Câmara 1'):
        camara, _ = Camara.objects.get_or_create(
            filial=self.filial, nome=nome,
            defaults={'tipo': Camara.Tipo.CONGELADOS},
        )
        return LoteArmazenado.objects.create(
            filial=self.filial, lote=lote, camara=camara, endereco='Rua 2 · A3',
        )

    def _pedido(self, quantidade=Decimal('100'), status=ST.CONFIRMADO,
                entrega=None, produto=None):
        pedido = PedidoVenda.objects.create(
            filial=self.filial,
            numero_pedido=f'PV{PedidoVenda.objects.count() + 1}',
            cliente=self.cliente, usuario=self.usuario, status=status,
            data_emissao=timezone.now(), data_entrega_prevista=entrega,
        )
        ItemPedidoVenda.objects.create(
            pedido=pedido, produto=produto or self.polpa, quantidade=quantidade,
            valor_unitario=Decimal('1'), valor_bruto=quantidade,
            valor_total=quantidade,
        )
        return pedido

    def _linha(self, pedido):
        return SeparacaoPolpaService.mapa(pedido)[0]


class FilaTests(SeparacaoBase):
    """Quais pedidos aparecem, e em que ordem."""

    def test_pedido_confirmado_entra_na_fila(self):
        pedido = self._pedido()

        linhas = SeparacaoPolpaService.pedidos(self.filial)

        self.assertEqual([l['pedido'] for l in linhas], [pedido])

    def test_rascunho_nao_entra(self):
        """Pedido que ainda não foi confirmado não é trabalho da câmara."""
        self._pedido(status=ST.RASCUNHO)

        self.assertEqual(SeparacaoPolpaService.pedidos(self.filial), [])

    def test_a_fila_e_ordenada_pela_entrega(self):
        """
        Quem separa trabalha contra a data em que o caminhão sai, não contra
        a data do pedido.
        """
        hoje = timezone.localdate()
        depois = self._pedido(entrega=hoje + timedelta(days=5))
        antes = self._pedido(entrega=hoje + timedelta(days=1))

        linhas = SeparacaoPolpaService.pedidos(self.filial)

        self.assertEqual([l['pedido'] for l in linhas], [antes, depois])

    def test_pedido_sem_data_de_entrega_vai_para_o_fim_e_nao_some(self):
        com_data = self._pedido(entrega=timezone.localdate() + timedelta(days=3))
        sem_data = self._pedido()

        linhas = SeparacaoPolpaService.pedidos(self.filial)

        self.assertEqual([l['pedido'] for l in linhas], [com_data, sem_data])

    def test_entrega_vencida_e_marcada(self):
        self._pedido(entrega=timezone.localdate() - timedelta(days=1))

        self.assertTrue(SeparacaoPolpaService.pedidos(self.filial)[0]['atrasado'])

    def test_pedido_de_produto_de_fora_do_vertical_nao_entra(self):
        outro = CatalogoService.salvar(self.filial, {
            'tipo': T.FRUTA, 'descricao': 'Manga in natura', 'codigo': 'MG',
            'unidade_medida': self.unidade,
        }).produto
        self._pedido(produto=outro)

        self.assertEqual(SeparacaoPolpaService.pedidos(self.filial), [])


class MapaTests(SeparacaoBase):
    """A folha que vai para a câmara."""

    def test_a_sugestao_segue_a_validade_mais_curta(self):
        longo = self._lote(Decimal('100'), dias_validade=200)
        curto = self._lote(Decimal('60'), dias_validade=10)
        pedido = self._pedido(Decimal('50'))

        linha = self._linha(pedido)

        self.assertEqual([e['lote'] for e in linha['escolhidos']], [curto])
        self.assertNotIn(longo, [e['lote'] for e in linha['escolhidos']])

    def test_o_endereco_vem_junto(self):
        """
        Sem endereço, separar vira procurar — e cada minuto de porta aberta
        é temperatura subindo em tudo que está dentro.
        """
        lote = self._lote(Decimal('100'))
        self._guardar(lote)
        pedido = self._pedido(Decimal('50'))

        escolha = self._linha(pedido)['escolhidos'][0]

        self.assertIn('Câmara 1', escolha['onde'])
        self.assertIn('Rua 2', escolha['onde'])

    def test_lote_sem_endereco_aparece_sem_endereco(self):
        self._lote(Decimal('100'))
        pedido = self._pedido(Decimal('50'))

        self.assertEqual(self._linha(pedido)['escolhidos'][0]['onde'], '')

    def test_os_dias_de_validade_vem_junto(self):
        self._lote(Decimal('100'), dias_validade=12)
        pedido = self._pedido(Decimal('50'))

        self.assertEqual(self._linha(pedido)['escolhidos'][0]['dias'], 12)

    def test_lote_bloqueado_nao_entra_na_separacao(self):
        """É assim que o lote barrado pela qualidade deixa de sair por engano."""
        self._lote(Decimal('100'), status=LoteProduto.Status.BLOQUEADO)
        pedido = self._pedido(Decimal('50'))

        linha = self._linha(pedido)

        self.assertEqual(linha['escolhidos'], [])
        self.assertEqual(linha['falta'], Decimal('50'))

    def test_lote_vencido_nao_entra(self):
        self._lote(Decimal('100'), dias_validade=-1)
        pedido = self._pedido(Decimal('50'))

        self.assertEqual(self._linha(pedido)['escolhidos'], [])

    def test_falta_de_saldo_aparece_em_vez_de_travar(self):
        """
        Quem está na doca precisa ler "faltam 40" antes do caminhão chegar —
        uma página que recusa abrir não avisa ninguém.
        """
        self._lote(Decimal('60'))
        pedido = self._pedido(Decimal('100'))

        linha = self._linha(pedido)

        self.assertEqual(sum(e['quantidade'] for e in linha['escolhidos']), Decimal('60'))
        self.assertEqual(linha['falta'], Decimal('40'))

    def test_a_sugestao_soma_lotes_ate_fechar_o_item(self):
        self._lote(Decimal('30'), dias_validade=10)
        self._lote(Decimal('80'), dias_validade=20)
        pedido = self._pedido(Decimal('100'))

        linha = self._linha(pedido)

        self.assertEqual(
            [e['quantidade'] for e in linha['escolhidos']],
            [Decimal('30'), Decimal('70')],
        )
        self.assertEqual(linha['falta'], Decimal('0'))

    def test_o_que_ja_foi_atendido_sai_da_conta(self):
        self._lote(Decimal('100'))
        pedido = self._pedido(Decimal('100'))
        item = pedido.itens.first()
        item.quantidade_atendida = Decimal('60')
        item.save(update_fields=['quantidade_atendida'])

        linha = self._linha(pedido)

        self.assertEqual(
            sum(e['quantidade'] for e in linha['escolhidos']), Decimal('40'),
        )


class FecharTests(SeparacaoBase):
    """Fechar a separação — delegando ao ERP."""

    def test_fechar_cria_o_documento_do_erp_com_os_lotes(self):
        lote = self._lote(Decimal('100'))
        pedido = self._pedido(Decimal('50'))

        separacao = SeparacaoPolpaService.separar(pedido, self.usuario)

        pedido.refresh_from_db()
        self.assertEqual(pedido.status, ST.EM_SEPARACAO)
        self.assertEqual(
            [i.lote for i in separacao.itens.all()], [lote],
        )

    def test_depois_de_fechada_a_tela_mostra_o_que_foi_e_nao_a_sugestao(self):
        """
        Reexibir a sugestão sobre uma separação fechada faria a tela
        discordar do documento que o faturamento vai ler.
        """
        primeiro = self._lote(Decimal('50'), dias_validade=10)
        pedido = self._pedido(Decimal('50'))
        SeparacaoPolpaService.separar(pedido, self.usuario)
        # Um lote mais novo chega depois: a sugestão mudaria, o documento não.
        self._lote(Decimal('80'), dias_validade=5)

        linha = self._linha(pedido)

        self.assertEqual([e['lote'] for e in linha['escolhidos']], [primeiro])

    def test_sem_saldo_a_separacao_e_recusada(self):
        self._lote(Decimal('10'))
        pedido = self._pedido(Decimal('50'))

        with self.assertRaises(DomainError):
            SeparacaoPolpaService.separar(pedido, self.usuario)

    def test_pedido_em_rascunho_nao_pode_ser_separado(self):
        self._lote(Decimal('100'))
        pedido = self._pedido(Decimal('50'), status=ST.RASCUNHO)

        with self.assertRaises(DomainError):
            SeparacaoPolpaService.separar(pedido, self.usuario)


class TelaTests(SeparacaoBase):
    """As duas telas."""

    def test_a_lista_abre(self):
        pedido = self._pedido()

        resposta = self.client.get(reverse('polpa:separacao'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, pedido.numero_pedido)

    def test_a_lista_nao_e_o_placeholder_em_construcao(self):
        resposta = self.client.get(
            reverse('polpa:item', args=['expedicao', 'separacao']),
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, 'Tela em construção')

    def test_a_folha_do_pedido_mostra_lote_e_endereco(self):
        lote = self._lote(Decimal('100'))
        self._guardar(lote)
        pedido = self._pedido(Decimal('50'))

        resposta = self.client.get(
            reverse('polpa:separacao-pedido', args=[pedido.pk]),
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, lote.numero_lote)
        self.assertContains(resposta, 'Rua 2')

    def test_fechar_pela_tela(self):
        self._lote(Decimal('100'))
        pedido = self._pedido(Decimal('50'))

        self.client.post(reverse('polpa:separacao-pedido', args=[pedido.pk]))

        pedido.refresh_from_db()
        self.assertEqual(pedido.status, ST.EM_SEPARACAO)
        self.assertTrue(pedido.separacoes.exists())

    def test_a_tela_explica_a_recusa_em_vez_de_estourar(self):
        self._lote(Decimal('10'))
        pedido = self._pedido(Decimal('50'))

        resposta = self.client.post(
            reverse('polpa:separacao-pedido', args=[pedido.pk]), follow=True,
        )

        self.assertEqual(resposta.status_code, 200)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, ST.CONFIRMADO)
