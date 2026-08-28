"""
A conferência / acerto da viagem.

A CONTA QUE PRECISA FECHAR:

    carga inicial = vendas + bonificações + retornos
                    + demais saídas devidamente justificadas

O QUE ESTES TESTES CERCAM:

  · A CONTA APARECE INTEIRA, e não só o veredito: "não confere" sem mostrar
    de onde saiu a diferença vira ordem, e ordem ninguém contesta quando
    está errada;

  · DIVERGÊNCIA BLOQUEIA O ENCERRAMENTO — e bloqueia no serviço, não na
    tela: quem insistir pela URL leva a mesma resposta que o botão escondido
    daria;

  · A MENSAGEM É UMA SÓ. A tela mostra a mesma frase que a recusa devolve;
    duas redações para a mesma recusa ensinariam que são dois problemas;

  · O SALDO POR PRODUTO NÃO VÊ A CARGA INTEIRA. Venda já faturada e
    bonificação endereçada não passam por ele — uma carga alterada por fora
    fecha produto a produto e mesmo assim não bate com o que subiu.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import ItemCarga, VendaViagem, Viagem
from apps.logistica.services.estoque_viagem import EstoqueViagemService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

ZERO = Decimal('0')
T = VendaViagem.Tipo


class AcertoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Acerto LTDA', nome_fantasia='Acerto',
            cnpj='41345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='41345678000272',
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
            email='acerto@rota.local', nome='Acerto', password='x' * 12,
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

    def _viagem_do_exemplo(self):
        """360 = 150 vendidas + 180 na rua + 10 bonificadas + 20 devolvidas."""
        viagem = self._viagem(venda='150', remessa='210')
        self._entregar_na_rua(viagem, '180')
        self._entregar_na_rua(viagem, '10', tipo=T.BONIFICACAO)
        ViagemService.registrar_retorno(
            viagem, self.produto, Decimal('20'), usuario=self.usuario,
        )
        return viagem

    def _tela(self, viagem):
        return self.client.get(
            reverse('logistica:viagem-acerto', args=[viagem.pk]),
        ).content.decode()


class ContaTests(AcertoBase):
    """A conta que a tela mostra."""

    def test_o_exemplo_da_especificacao_linha_a_linha(self):
        viagem = self._viagem_do_exemplo()

        linhas = EstoqueViagemService.acerto(
            EstoqueViagemService.quadro(viagem),
        )

        self.assertEqual(
            [(l['rotulo'], l['quantidade']) for l in linhas],
            [
                ('Vendas já realizadas', Decimal('150')),
                ('Vendas durante a viagem', Decimal('180')),
                ('Bonificações', Decimal('10')),
                ('Retorno', Decimal('20')),
            ],
        )

    def test_as_linhas_vazias_somem(self):
        """
        Cinco zeros na tela escondem o número que importa — e a conta
        continua verdadeira sem eles.
        """
        viagem = self._viagem(venda='100')

        linhas = EstoqueViagemService.acerto(
            EstoqueViagemService.quadro(viagem),
        )

        self.assertEqual([l['rotulo'] for l in linhas], ['Vendas já realizadas'])

    def test_o_que_esta_no_caminhao_aparece_como_linha(self):
        """É ele que explica por que o total ainda não fecha."""
        viagem = self._viagem(remessa='200')
        self._entregar_na_rua(viagem, '50')

        linhas = EstoqueViagemService.acerto(
            EstoqueViagemService.quadro(viagem),
        )
        rotulos = {l['rotulo']: l['quantidade'] for l in linhas}

        self.assertEqual(rotulos['Ainda no caminhão'], Decimal('150'))

    def test_a_soma_das_linhas_e_a_carga_inicial(self):
        viagem = self._viagem_do_exemplo()
        quadro = EstoqueViagemService.quadro(viagem)

        soma = sum(
            (l['quantidade'] for l in EstoqueViagemService.acerto(quadro)), ZERO,
        )

        self.assertEqual(soma, quadro['carga_inicial'])


class BloqueioTests(AcertoBase):
    """Divergência não encerra."""

    def test_com_mercadoria_no_caminhao_a_viagem_nao_encerra(self):
        viagem = self._viagem(remessa='200')
        self._entregar_na_rua(viagem, '50')

        with self.assertRaises(DadosInvalidosError) as erro:
            ViagemService.encerrar(viagem)

        self.assertIn(ViagemService.NAO_CONCILIADA, str(erro.exception))

    def test_carga_alterada_por_fora_tambem_bloqueia(self):
        """
        O SALDO POR PRODUTO SÓ SABE O QUE ELE MESMO REGISTRA. Se a carga
        for alterada depois de fechada, o saldo continua fechando produto a
        produto e mesmo assim faltam 40 unidades do que subiu no caminhão —
        sem a conta agregada, esta viagem encerraria com esse buraco.
        """
        viagem = self._viagem_do_exemplo()
        item = ItemCarga.objects.get(viagem=viagem, natureza=self.remessa)
        ItemCarga.objects.filter(pk=item.pk).update(
            quantidade=item.quantidade + Decimal('40'),
        )

        with self.assertRaises(DadosInvalidosError) as erro:
            ViagemService.encerrar(viagem)

        self.assertIn(ViagemService.NAO_CONCILIADA, str(erro.exception))
        viagem.refresh_from_db()
        self.assertNotEqual(viagem.status, Viagem.Status.FINALIZADA)

    def test_com_a_conta_fechada_a_viagem_encerra(self):
        viagem = self._viagem_do_exemplo()

        ViagemService.encerrar(viagem)

        viagem.refresh_from_db()
        self.assertEqual(viagem.status, Viagem.Status.FINALIZADA)


class TelaTests(AcertoBase):
    """O que a pessoa vê."""

    def test_a_tela_mostra_a_conta_e_o_total(self):
        viagem = self._viagem_do_exemplo()

        html = self._tela(viagem)

        self.assertIn('Conferência / acerto da viagem', html)
        for numero in ('360', '150', '180', '10', '20'):
            self.assertIn(numero, html)
        self.assertIn('Encerrar viagem', html)

    def test_com_divergencia_a_tela_diz_a_mensagem_e_esconde_o_botao(self):
        viagem = self._viagem(remessa='200')
        self._entregar_na_rua(viagem, '50')

        html = self._tela(viagem)

        self.assertIn(
            'A quantidade da carga não foi totalmente conciliada.', html,
        )
        self.assertNotIn('Encerrar viagem', html)

    def test_encerrar_pela_tela(self):
        viagem = self._viagem_do_exemplo()

        self.client.post(reverse('logistica:viagem-acerto', args=[viagem.pk]))

        viagem.refresh_from_db()
        self.assertEqual(viagem.status, Viagem.Status.FINALIZADA)

    def test_insistir_pela_url_leva_a_mesma_recusa(self):
        """
        Quem recusa é o serviço, e não o botão escondido: esconder o botão
        sem a regra atrás seria trancar a porta e deixar a janela aberta.
        """
        viagem = self._viagem(remessa='200')
        self._entregar_na_rua(viagem, '50')

        resposta = self.client.post(
            reverse('logistica:viagem-acerto', args=[viagem.pk]), follow=True,
        )

        viagem.refresh_from_db()
        self.assertNotEqual(viagem.status, Viagem.Status.FINALIZADA)
        self.assertContains(
            resposta, 'A quantidade da carga não foi totalmente conciliada.',
        )

    def test_encerrada_a_tela_vira_prestacao_de_contas(self):
        viagem = self._viagem_do_exemplo()
        ViagemService.encerrar(viagem)

        html = self._tela(viagem)

        self.assertNotIn('Encerrar viagem', html)
        self.assertIn('prestação de contas', html)
