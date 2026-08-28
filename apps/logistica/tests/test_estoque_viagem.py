"""
Onde foi parar cada unidade que subiu no caminhão.

O EXEMPLO DA ESPECIFICAÇÃO: carga de 360, com 150 de venda já realizada,
180 vendidas na rua, 10 bonificadas e 20 devolvidas — fecha em 360.

O QUE ESTES TESTES CERCAM:

  · A CARGA TEM QUE FECHAR. Enquanto a conta não fecha, existe mercadoria
    da empresa sem destino registrado — e o buraco não é de relatório, é de
    mercadoria;

  · AS QUATRO COLUNAS SÃO SEPARADAS porque são naturezas diferentes: venda
    já realizada saiu endereçada; venda fora saiu sem comprador e o que
    importa é quanto ainda dá para vender; bonificação saiu de graça;
    retorno é o que voltou. Somá-las esconderia o que a operação precisa
    ver;

  · BONIFICAÇÃO QUE VOLTOU CONTA COMO RETORNO, e não como bonificação:
    contá-la nas duas faria a soma passar da carga inicial, e a cortesia que
    voltou não foi dada a ninguém;

  · O NÚMERO SOZINHO NÃO DIZ O QUE FAZER — a pendência diz.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import EntregaBonificacao, VendaViagem, Viagem
from apps.logistica.services.entrega_bonificacao import (
    EntregaBonificacaoService,
)
from apps.logistica.services.estoque_viagem import EstoqueViagemService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

ZERO = Decimal('0')
T = VendaViagem.Tipo


class EstoqueViagemBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Carga Fecha LTDA', nome_fantasia='Carga',
            cnpj='12945678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='12945678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='doca@carga.local', nome='Doca', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)

        cls.venda = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='venda', descricao='Venda',
            especie=NaturezaOperacao.Especie.VENDA,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.venda, cfop='5102')
        cls.remessa = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='remessa', descricao='Remessa',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.remessa, cfop='5904')
        cls.bonificacao = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='bonif', descricao='Bonificação',
            especie=NaturezaOperacao.Especie.BONIFICACAO,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.bonificacao, cfop='5910')

    def setUp(self):
        self.client.force_login(self.usuario)
        self.produto = self._produto('P1', '5000')

    # ── Fixtures ─────────────────────────────────────────────────────────

    def _produto(self, codigo, saldo):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade,
            descricao=f'Produto {codigo}', codigo=codigo, ncm='20079900',
            controla_lote=False, preco_venda=Decimal('10'),
            preco_custo=Decimal('4'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(saldo), usuario_id=self.usuario.pk,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
        )
        return produto

    def _viagem(self, venda='0', remessa='0', bonificacao='0'):
        viagem = Viagem.objects.create(
            filial=self.filial, numero=Viagem.objects.count() + 1,
            motorista_nome='Seu Zé', veiculo_placa='ABC1D23',
            vendedor=self.usuario, responsavel=self.usuario,
        )
        for natureza, quantidade, cliente in (
            (self.venda, venda, self.cliente),
            (self.remessa, remessa, None),
            (self.bonificacao, bonificacao, self.cliente),
        ):
            if Decimal(quantidade) <= ZERO:
                continue
            ViagemService.adicionar_item(viagem, {
                'natureza': natureza, 'produto': self.produto,
                'cliente': cliente, 'quantidade': quantidade,
                'valor_unitario': '10',
            })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])
        return viagem

    def _entregar_na_rua(self, viagem, quantidade, tipo=T.VENDA):
        dados = {
            'tipo': tipo, 'produto': self.produto, 'quantidade': quantidade,
            'valor_unitario': '10', 'cliente': self.cliente,
        }
        if tipo == T.BONIFICACAO:
            dados['motivo'] = VendaViagem.Motivo.BRINDE
        return VendaViagemService.registrar(viagem, dados, usuario=self.usuario)


class ExemploDaEspecificacaoTests(EstoqueViagemBase):
    """360 = 150 + 180 + 10 + 20."""

    def test_a_carga_de_trezentos_e_sessenta_fecha(self):
        # 150 já vendidas na fábrica + 210 sem comprador.
        viagem = self._viagem(venda='150', remessa='210')
        self._entregar_na_rua(viagem, '180')
        self._entregar_na_rua(viagem, '10', tipo=T.BONIFICACAO)
        ViagemService.registrar_retorno(
            viagem, self.produto, Decimal('20'), usuario=self.usuario,
        )

        quadro = EstoqueViagemService.quadro(viagem)

        self.assertEqual(quadro['carga_inicial'], Decimal('360'))
        self.assertEqual(quadro['vendas_realizadas'], Decimal('150'))
        self.assertEqual(quadro['venda_na_rua'], Decimal('180'))
        self.assertEqual(quadro['bonificacao'], Decimal('10'))
        self.assertEqual(quadro['retorno'], Decimal('20'))
        self.assertEqual(quadro['destinos'], Decimal('360'))
        self.assertTrue(quadro['fecha'])


class ColunasTests(EstoqueViagemBase):
    """Cada natureza na sua coluna."""

    def test_a_venda_ja_realizada_nao_se_mistura_com_a_da_rua(self):
        """
        Uma saiu endereçada, a outra saiu sem comprador: somá-las esconderia
        quanto ainda dá para vender.
        """
        viagem = self._viagem(venda='100', remessa='200')
        self._entregar_na_rua(viagem, '50')

        quadro = EstoqueViagemService.quadro(viagem)

        self.assertEqual(quadro['vendas_realizadas'], Decimal('100'))
        self.assertEqual(quadro['venda_na_rua'], Decimal('50'))

    def test_o_disponivel_para_novas_vendas_e_o_saldo_da_viagem(self):
        viagem = self._viagem(remessa='200')
        self._entregar_na_rua(viagem, '50')

        quadro = EstoqueViagemService.quadro(viagem)

        self.assertEqual(quadro['disponivel_para_venda'], Decimal('150'))

    def test_a_bonificacao_soma_a_da_carga_com_a_da_rua(self):
        viagem = self._viagem(remessa='200', bonificacao='30')
        self._entregar_na_rua(viagem, '10', tipo=T.BONIFICACAO)

        quadro = EstoqueViagemService.quadro(viagem)

        self.assertEqual(quadro['bonificacao_da_carga'], Decimal('30'))
        self.assertEqual(quadro['bonificacao_na_rua'], Decimal('10'))
        self.assertEqual(quadro['bonificacao'], Decimal('40'))

    def test_a_carga_sem_movimento_esta_toda_em_poder(self):
        viagem = self._viagem(remessa='200')

        quadro = EstoqueViagemService.quadro(viagem)

        self.assertEqual(quadro['em_poder'], Decimal('200'))
        self.assertTrue(quadro['fecha'])


class BonificacaoQueVoltouTests(EstoqueViagemBase):
    """A cortesia que não foi dada a ninguém."""

    def test_ela_conta_como_retorno_e_nao_como_bonificacao(self):
        """
        Contá-la nas duas colunas faria a soma passar da carga inicial.
        """
        viagem = self._viagem(bonificacao='20')
        item = viagem.itens.first()
        entrega = EntregaBonificacaoService.para_item(item)
        EntregaBonificacaoService.mover(entrega, EntregaBonificacao.Status.EM_TRANSPORTE)
        EntregaBonificacaoService.mover(
            entrega, EntregaBonificacao.Status.RECUSADA,
            {'motivo_nao_entrega': EntregaBonificacao.MotivoNaoEntrega.AUSENTE},
        )
        EntregaBonificacaoService.mover(
            entrega, EntregaBonificacao.Status.RETORNO_PENDENTE,
        )
        EntregaBonificacaoService.tratar_retorno(entrega, usuario=self.usuario)

        quadro = EstoqueViagemService.quadro(viagem)

        self.assertEqual(quadro['bonificacao'], ZERO)
        self.assertEqual(quadro['retorno'], Decimal('20'))
        self.assertEqual(quadro['carga_inicial'], Decimal('20'))
        self.assertTrue(quadro['fecha'])


class PendenciaTests(EstoqueViagemBase):
    """O que impede a carga de fechar, em português."""

    def test_mercadoria_em_poder_vira_pendencia_com_o_caminho(self):
        """
        "Faltam 30" é o começo da pergunta; a pendência é a resposta.
        """
        viagem = self._viagem(remessa='200')
        self._entregar_na_rua(viagem, '170')

        pendencias = EstoqueViagemService.pendencias(
            EstoqueViagemService.quadro(viagem),
        )

        self.assertEqual(len(pendencias), 1)
        self.assertIn('30', pendencias[0])
        self.assertIn('registre venda', pendencias[0])

    def test_carga_fechada_nao_tem_pendencia(self):
        viagem = self._viagem(venda='100')

        self.assertEqual(
            EstoqueViagemService.pendencias(
                EstoqueViagemService.quadro(viagem),
            ),
            [],
        )


class TelaTests(EstoqueViagemBase):
    """O quadro na tela da viagem."""

    def test_a_tela_mostra_as_quatro_colunas(self):
        viagem = self._viagem(venda='150', remessa='210')
        self._entregar_na_rua(viagem, '180')

        html = self.client.get(
            reverse('logistica:viagem-detail', args=[viagem.pk]),
        ).content.decode()

        self.assertIn('Estoque durante a viagem', html)
        for coluna in (
            'Carga inicial', 'Vendas já realizadas', 'Venda na rua',
            'Bonificação', 'Retorno',
        ):
            self.assertIn(coluna, html, f'a coluna {coluna} sumiu')

    def test_a_tela_diz_quando_a_carga_fecha(self):
        viagem = self._viagem(venda='150')

        html = self.client.get(
            reverse('logistica:viagem-detail', args=[viagem.pk]),
        ).content.decode()

        self.assertIn('a carga fecha', html)

    def test_a_tela_cobra_o_que_esta_em_poder(self):
        viagem = self._viagem(remessa='200')

        html = self.client.get(
            reverse('logistica:viagem-detail', args=[viagem.pk]),
        ).content.decode()

        self.assertIn('ainda em poder da viagem', html)
        self.assertIn('disponível para novas vendas', html)
