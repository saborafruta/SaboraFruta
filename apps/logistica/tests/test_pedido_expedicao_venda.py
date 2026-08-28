"""
A venda que originou a expedição — e o cliente que vem dela.

O QUE ESTA TELA REDIGITAVA

O pedido de expedição pedia o cliente de novo, com a venda ao lado sabendo
quem era. Cada redigitação é uma chance de expedir a mercadoria de alguém
para outra pessoa — e o pedido continua parecendo certo em todo relatório,
porque nada no sistema liga uma coisa à outra.

O QUE ESTES TESTES CERCAM:

  · O CLIENTE DA EXPEDIÇÃO É O CLIENTE DA VENDA. Não é preenchimento de
    tela: é regra do formulário, e vale para quem chega por outro caminho;

  · SEM VENDA, O CLIENTE VOLTA A SER OBRIGATÓRIO. Carga avulsa — amostra,
    reposição, brinde — existe e também precisa de destinatário;

  · PEDIDO CANCELADO NÃO É OFERECIDO: expedir o que foi cancelado é entregar
    mercadoria que ninguém mais comprou;

  · A TELA MOSTRA O CAMPO e traz as vendas da filial, sem vazar as de outra.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.logistica.forms import (
    ItemPedidoExpedicaoForm, PedidoExpedicaoForm,
)
from apps.logistica.models import PedidoExpedicao
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import MovimentacaoEstoque
from apps.logistica.services.itens_da_venda import ItensDaVendaService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial
from apps.vendas.models import ItemPedidoVenda, PedidoVenda


class ExpedicaoDaVendaBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Expedição LTDA', nome_fantasia='Expedição',
            cnpj='31145678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='31145678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='expedicao@rota.local', nome='Expedição', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.outro_cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Padaria do Bairro',
            cpf_cnpj='98765432100', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.outro_cliente, filial=cls.filial)

    def setUp(self):
        self.client.force_login(self.usuario)

    # ── Fixtures ─────────────────────────────────────────────────────────

    def _venda(self, cliente=None, numero='1001', status=None):
        return PedidoVenda.objects.create(
            filial=self.filial,
            numero_pedido=numero,
            cliente=cliente or self.cliente,
            data_emissao=timezone.now(),
            status=status or PedidoVenda.Status.CONFIRMADO,
            usuario=self.usuario,
            valor_total=Decimal('500.00'),
        )

    def _dados(self, **extra):
        dados = {
            'numero': '1',
            'data_pedido': timezone.localdate().isoformat(),
            'status': PedidoExpedicao.Status.ABERTO,
            'prioridade': PedidoExpedicao.Prioridade.NORMAL,
        }
        dados.update(extra)
        return dados


class ClienteDaVendaTests(ExpedicaoDaVendaBase):
    """A regra: o cliente da expedição é o cliente da venda."""

    def test_escolher_a_venda_dispensa_escolher_o_cliente(self):
        venda = self._venda()

        form = PedidoExpedicaoForm(
            self._dados(pedido_venda=venda.pk), filial=self.filial,
        )

        self.assertTrue(form.is_valid(), form.errors)
        pedido = form.save(commit=False)
        pedido.filial = self.filial
        pedido.save()
        self.assertEqual(pedido.cliente, self.cliente)
        self.assertEqual(pedido.pedido_venda, venda)

    def test_a_venda_manda_no_cliente_mesmo_se_vier_outro_no_post(self):
        """
        Expedir para um cliente diferente do que comprou seria entregar a
        mercadoria de alguém a outra pessoa — e o pedido continuaria
        parecendo certo em todo relatório.
        """
        venda = self._venda()

        form = PedidoExpedicaoForm(
            self._dados(
                pedido_venda=venda.pk, cliente=self.outro_cliente.pk,
            ),
            filial=self.filial,
        )

        self.assertTrue(form.is_valid(), form.errors)
        pedido = form.save(commit=False)
        pedido.filial = self.filial
        pedido.save()
        self.assertEqual(pedido.cliente, self.cliente)

    def test_sem_venda_o_cliente_continua_obrigatorio(self):
        """Carga avulsa também precisa de destinatário."""
        form = PedidoExpedicaoForm(self._dados(), filial=self.filial)

        self.assertFalse(form.is_valid())
        self.assertIn('cliente', form.errors)
        self.assertIn('pedido de venda', form.errors['cliente'][0])

    def test_carga_avulsa_sai_sem_venda(self):
        """Amostra, reposição e brinde não nascem de pedido nenhum."""
        form = PedidoExpedicaoForm(
            self._dados(cliente=self.cliente.pk), filial=self.filial,
        )

        self.assertTrue(form.is_valid(), form.errors)
        pedido = form.save(commit=False)
        pedido.filial = self.filial
        pedido.save()
        self.assertIsNone(pedido.pedido_venda)
        self.assertEqual(pedido.cliente, self.cliente)

    def test_pedido_cancelado_nao_e_oferecido(self):
        cancelado = self._venda(numero='2002', status=PedidoVenda.Status.CANCELADO)

        form = PedidoExpedicaoForm(filial=self.filial)

        self.assertNotIn(
            cancelado, form.fields['pedido_venda'].queryset,
        )

    def test_venda_de_outra_filial_nao_entra(self):
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Filial 2',
            cnpj='31145678000353', uf='RN', cidade='Mossoró',
        )
        alheia = PedidoVenda.objects.create(
            filial=outra, numero_pedido='3003', cliente=self.cliente,
            data_emissao=timezone.now(), status=PedidoVenda.Status.CONFIRMADO,
            usuario=self.usuario,
        )

        form = PedidoExpedicaoForm(filial=self.filial)

        self.assertNotIn(alheia, form.fields['pedido_venda'].queryset)


class TelaTests(ExpedicaoDaVendaBase):
    """O campo aparece e traz as vendas da filial."""

    def test_a_tela_de_novo_pedido_mostra_o_campo_da_venda(self):
        venda = self._venda()

        html = self.client.get(
            reverse('logistica:pedido-expedicao-create'),
        ).content.decode()

        self.assertIn('Pedido de venda', html)
        self.assertIn('Buscar pedido ou cliente', html)
        self.assertIn(venda.numero_pedido, html)

    def test_a_tela_diz_de_onde_vem_o_cliente(self):
        html = self.client.get(
            reverse('logistica:pedido-expedicao-create'),
        ).content.decode()

        self.assertIn('Venda e cliente', html)
        self.assertIn('o cliente vem dele', html)

    def test_criar_pela_tela_com_a_venda_escolhida(self):
        venda = self._venda()

        self.client.post(
            reverse('logistica:pedido-expedicao-create'),
            self._dados(pedido_venda=venda.pk),
            follow=True,
        )

        pedido = PedidoExpedicao.objects.get(filial=self.filial)
        self.assertEqual(pedido.pedido_venda, venda)
        self.assertEqual(pedido.cliente, self.cliente)


class ItensDaVendaBase(ExpedicaoDaVendaBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='CX', descricao='Caixa',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade, descricao='Polpa de Caju',
            codigo='P-100', ncm='20079900', controla_lote=False,
            preco_venda=Decimal('10'), peso_bruto=Decimal('1.200'),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)
        cls.outro_produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade, descricao='Polpa de Manga',
            codigo='P-200', ncm='20079900', controla_lote=False,
            preco_venda=Decimal('8'),
        )
        ProdutoFilial.objects.create(produto=cls.outro_produto, filial=cls.filial)

    # ── Fixtures ─────────────────────────────────────────────────────────

    def _venda_com_itens(self, *quantidades, numero='1001'):
        """Uma venda com uma linha por quantidade informada."""
        venda = self._venda(numero=numero)
        produtos = (self.produto, self.outro_produto)
        for indice, quantidade in enumerate(quantidades):
            produto = produtos[indice % len(produtos)]
            ItemPedidoVenda.objects.create(
                pedido=venda, produto=produto, numero_item=indice + 1,
                quantidade=Decimal(quantidade),
                valor_unitario=Decimal('10'),
                valor_bruto=Decimal(quantidade) * 10,
                valor_total=Decimal(quantidade) * 10,
            )
        return venda

    def _expedicao(self, venda=None, numero=1):
        return PedidoExpedicao.objects.create(
            filial=self.filial, numero=numero, cliente=self.cliente,
            pedido_venda=venda, responsavel=self.usuario,
        )


class TrazerItensTests(ItensDaVendaBase):
    """O que vem da venda."""

    def test_traz_as_linhas_da_venda_com_produto_quantidade_e_preco(self):
        venda = self._venda_com_itens('100', '50')
        pedido = self._expedicao(venda)

        resultado = ItensDaVendaService.trazer(pedido, usuario=self.usuario)

        self.assertEqual(len(resultado['criadas']), 2)
        linhas = list(pedido.itens.order_by('ordem'))
        self.assertEqual(linhas[0].produto_nome, 'Polpa de Caju')
        self.assertEqual(linhas[0].produto_codigo, 'P-100')
        self.assertEqual(linhas[0].quantidade, Decimal('100'))
        self.assertEqual(linhas[0].unidade, 'CX')
        self.assertEqual(linhas[0].valor_unitario, Decimal('10'))
        self.assertEqual(linhas[1].quantidade, Decimal('50'))

    def test_cada_linha_sabe_de_qual_linha_da_venda_veio(self):
        venda = self._venda_com_itens('100')
        pedido = self._expedicao(venda)

        ItensDaVendaService.trazer(pedido, usuario=self.usuario)

        item = pedido.itens.get()
        self.assertEqual(item.item_venda, venda.itens.get())

    def test_o_peso_vem_do_cadastro_do_produto(self):
        """É o que a balança e o MDF-e vão cobrar depois."""
        venda = self._venda_com_itens('10')
        pedido = self._expedicao(venda)

        ItensDaVendaService.trazer(pedido, usuario=self.usuario)

        self.assertEqual(pedido.itens.get().peso_kg, Decimal('12.000'))

    def test_os_totais_do_pedido_sao_recalculados(self):
        venda = self._venda_com_itens('100', '50')
        pedido = self._expedicao(venda)

        ItensDaVendaService.trazer(pedido, usuario=self.usuario)

        pedido.refresh_from_db()
        self.assertEqual(pedido.valor_total, Decimal('1500.00'))

    def test_clicar_duas_vezes_nao_duplica_a_carga(self):
        venda = self._venda_com_itens('100')
        pedido = self._expedicao(venda)
        ItensDaVendaService.trazer(pedido, usuario=self.usuario)

        with self.assertRaises(DadosInvalidosError) as erro:
            ItensDaVendaService.trazer(pedido, usuario=self.usuario)

        self.assertIn('já estão em pedidos de expedição', str(erro.exception))
        self.assertEqual(pedido.itens.count(), 1)


class SaldoDaVendaTests(ItensDaVendaBase):
    """Uma venda pode sair em duas viagens."""

    def test_a_segunda_expedicao_traz_so_o_que_falta(self):
        """
        Repetir a quantidade cheia faria o cliente receber o dobro do que
        comprou.
        """
        venda = self._venda_com_itens('100')
        primeira = self._expedicao(venda, numero=1)
        item = primeira.itens.create(
            item_venda=venda.itens.get(), ordem=1,
            produto_nome='Polpa de Caju', quantidade=Decimal('60'),
        )
        segunda = self._expedicao(venda, numero=2)

        ItensDaVendaService.trazer(segunda, usuario=self.usuario)

        self.assertEqual(segunda.itens.get().quantidade, Decimal('40'))
        self.assertEqual(item.quantidade, Decimal('60'))

    def test_expedicao_cancelada_devolve_o_saldo(self):
        """O que estava nela voltou a ser saldo da venda."""
        venda = self._venda_com_itens('100')
        cancelada = self._expedicao(venda, numero=1)
        cancelada.itens.create(
            item_venda=venda.itens.get(), ordem=1,
            produto_nome='Polpa de Caju', quantidade=Decimal('100'),
        )
        cancelada.status = PedidoExpedicao.Status.CANCELADO
        cancelada.save(update_fields=['status'])
        nova = self._expedicao(venda, numero=2)

        ItensDaVendaService.trazer(nova, usuario=self.usuario)

        self.assertEqual(nova.itens.get().quantidade, Decimal('100'))

    def test_o_resumo_mostra_o_que_ja_foi_e_o_que_falta(self):
        venda = self._venda_com_itens('100')
        primeira = self._expedicao(venda, numero=1)
        primeira.itens.create(
            item_venda=venda.itens.get(), ordem=1,
            produto_nome='Polpa de Caju', quantidade=Decimal('30'),
        )
        segunda = self._expedicao(venda, numero=2)

        resumo = ItensDaVendaService.resumo(segunda)

        linha = resumo['linhas'][0]
        self.assertEqual(linha['vendida'], Decimal('100'))
        self.assertEqual(linha['expedida'], Decimal('30'))
        self.assertEqual(linha['saldo'], Decimal('70'))
        self.assertTrue(resumo['tem_pendencia'])


class RecusaTests(ItensDaVendaBase):
    """Quando trazer não faz sentido."""

    def test_pedido_sem_venda_nao_tem_o_que_trazer(self):
        pedido = self._expedicao()

        with self.assertRaises(DadosInvalidosError) as erro:
            ItensDaVendaService.trazer(pedido, usuario=self.usuario)

        self.assertIn('não veio de um pedido de venda', str(erro.exception))

    def test_pedido_ja_expedido_nao_recebe_item_novo(self):
        """Acrescentar linha depois seria reescrever o que o caminhão levou."""
        venda = self._venda_com_itens('100')
        pedido = self._expedicao(venda)
        pedido.status = PedidoExpedicao.Status.EXPEDIDO
        pedido.save(update_fields=['status'])

        with self.assertRaises(DadosInvalidosError) as erro:
            ItensDaVendaService.trazer(pedido, usuario=self.usuario)

        self.assertIn('não recebe', str(erro.exception))
        self.assertEqual(pedido.itens.count(), 0)

    def test_o_servico_nao_mexe_no_estoque(self):
        """
        Trazer item para a expedição é planejamento: quem tira mercadoria do
        estoque é a viagem ou o faturamento.
        """
        venda = self._venda_com_itens('100')
        pedido = self._expedicao(venda)
        antes = MovimentacaoEstoque.objects.count()

        ItensDaVendaService.trazer(pedido, usuario=self.usuario)

        self.assertEqual(MovimentacaoEstoque.objects.count(), antes)


class TelaDosItensTests(ItensDaVendaBase):
    """O botão e a prévia do que viria."""

    def _detalhe(self, pedido):
        return self.client.get(
            reverse('logistica:pedido-expedicao-detail', args=[pedido.pk]),
        ).content.decode()

    def test_a_tela_mostra_o_que_viria_antes_de_vir(self):
        venda = self._venda_com_itens('100', '50')
        pedido = self._expedicao(venda)

        html = self._detalhe(pedido)

        self.assertIn('Trazer 2 item(ns) da venda', html)
        self.assertIn('Polpa de Caju', html)
        self.assertIn('Polpa de Manga', html)

    def test_trazer_pela_tela(self):
        venda = self._venda_com_itens('100')
        pedido = self._expedicao(venda)

        self.client.post(
            reverse('logistica:pedido-expedicao-itens-da-venda', args=[pedido.pk]),
            follow=True,
        )

        self.assertEqual(pedido.itens.count(), 1)

    def test_sem_saldo_o_botao_some(self):
        venda = self._venda_com_itens('100')
        pedido = self._expedicao(venda)
        ItensDaVendaService.trazer(pedido, usuario=self.usuario)

        html = self._detalhe(pedido)

        self.assertNotIn('Trazer 1 item(ns) da venda', html)
        self.assertIn('já está em expedição', html)

    def test_pedido_sem_venda_nao_oferece_o_botao(self):
        pedido = self._expedicao()

        html = self._detalhe(pedido)

        self.assertNotIn('da venda', html)


class ValorDaLinhaTests(ItensDaVendaBase):
    """
    O valor da linha é o preço vezes o que se cobra dela.

    NA EXPEDIÇÃO, O QUE SE COBRA É O VOLUME. A quantidade descreve o conteúdo
    — "tangerina 400 g", 1 unidade — e o que sai no caminhão são as caixas.
    """

    def test_volume_vezes_preco(self):
        pedido = self._expedicao()

        item = pedido.itens.create(
            ordem=1, produto_nome='Tangerina 400 g',
            quantidade=Decimal('1'), unidade='G',
            volumes=Decimal('5'), valor_unitario=Decimal('6.80'),
        )

        self.assertEqual(item.valor_total, Decimal('34.00'))

    def test_sem_volume_a_quantidade_manda(self):
        """
        Linha lançada antes deste campo existir, ou carga que não se conta em
        caixas, continua valendo preço × quantidade — trocar o multiplicador
        para zero zeraria pedido que já estava certo.
        """
        pedido = self._expedicao()

        item = pedido.itens.create(
            ordem=1, produto_nome='Polpa a granel',
            quantidade=Decimal('12'), volumes=Decimal('0'),
            valor_unitario=Decimal('2.50'),
        )

        self.assertEqual(item.valor_total, Decimal('30.00'))

    def test_o_total_do_pedido_soma_as_linhas(self):
        pedido = self._expedicao()
        pedido.itens.create(
            ordem=1, produto_nome='Tangerina 400 g', quantidade=Decimal('1'),
            volumes=Decimal('5'), valor_unitario=Decimal('6.80'),
        )
        pedido.itens.create(
            ordem=2, produto_nome='Caju 1 kg', quantidade=Decimal('1'),
            volumes=Decimal('2'), valor_unitario=Decimal('10.00'),
        )

        pedido.recalcular_totais()

        pedido.refresh_from_db()
        self.assertEqual(pedido.valor_total, Decimal('54.00'))
        self.assertEqual(pedido.volumes, Decimal('7'))


class PesoEmGramasTests(ItensDaVendaBase):
    """400 gramas são 0,400 kg — e o campo precisa aceitar isso."""

    def test_o_peso_guarda_as_tres_casas(self):
        pedido = self._expedicao()

        item = pedido.itens.create(
            ordem=1, produto_nome='Tangerina 400 g', quantidade=Decimal('1'),
            volumes=Decimal('1'), peso_kg=Decimal('0.400'),
        )

        item.refresh_from_db()
        self.assertEqual(item.peso_kg, Decimal('0.400'))

    def test_o_formulario_aceita_gramas(self):
        form = ItemPedidoExpedicaoForm({
            'produto_nome': 'Tangerina 400 g',
            'quantidade': '1',
            'unidade': 'UN',
            'volumes': '5',
            'peso_kg': '0,400',
            'valor_unitario': '6,80',
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['peso_kg'], Decimal('0.400'))

    def test_o_campo_de_peso_pede_tres_casas(self):
        """
        Um `step` de duas casas faria o navegador recusar 0,400 — e a recusa
        aparece como campo vermelho sem explicação.
        """
        form = ItemPedidoExpedicaoForm()

        self.assertEqual(form.fields['peso_kg'].widget.attrs['step'], '0.001')

    def test_a_tela_mostra_o_peso_em_tres_casas(self):
        pedido = self._expedicao()
        pedido.itens.create(
            ordem=1, produto_nome='Tangerina 400 g', quantidade=Decimal('1'),
            volumes=Decimal('1'), peso_kg=Decimal('0.400'),
        )
        pedido.recalcular_totais()

        html = self.client.get(
            reverse('logistica:pedido-expedicao-detail', args=[pedido.pk]),
        ).content.decode()

        self.assertIn('0,400', html)
