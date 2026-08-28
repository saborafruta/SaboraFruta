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
from apps.financeiro.constants.enums import StatusContaReceber
from apps.financeiro.models.formas_pagamento import (
    CondicaoPagamento, FormaPagamento,
)
from apps.logistica.forms import (
    ItemPedidoExpedicaoForm, PedidoExpedicaoForm,
)
from apps.logistica.models import (
    ItemRomaneioCarga, PedidoExpedicao, RomaneioCarga,
)
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import MovimentacaoEstoque
from apps.logistica.services.financeiro_expedicao import (
    FinanceiroExpedicaoService,
)
from apps.logistica.services.itens_da_venda import ItensDaVendaService
from apps.logistica.services.romaneio_do_pedido import (
    RomaneioDoPedidoService,
)
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


class EntregaNoRomaneioTests(ItensDaVendaBase):
    """
    O pedido vinculado ao romaneio vira entrega.

    VINCULAR SEM CRIAR A ENTREGA deixava o motorista sair sem endereço — o
    ponteiro dizia "esta carga vai nesse romaneio" e a parada não existia lá.
    """

    def _romaneio(self, numero=1):
        return RomaneioCarga.objects.create(
            filial=self.filial, numero=numero, responsavel=self.usuario,
        )

    def _pedido_com_carga(self, romaneio=None):
        pedido = self._expedicao()
        pedido.romaneio = romaneio
        pedido.endereco_entrega = {
            'endereco': 'Rua Áustria', 'numero': '115',
            'bairro': 'Passagem de Areia', 'cidade': 'Parnamirim', 'uf': 'RN',
        }
        pedido.save()
        pedido.itens.create(
            ordem=1, produto_nome='Tangerina 400 g', quantidade=Decimal('1'),
            volumes=Decimal('5'), peso_kg=Decimal('2.000'),
            valor_unitario=Decimal('6.80'),
        )
        pedido.recalcular_totais()
        pedido.refresh_from_db()
        return pedido

    def test_vincular_cria_a_entrega_com_cliente_endereco_e_totais(self):
        romaneio = self._romaneio()
        pedido = self._pedido_com_carga(romaneio)

        RomaneioDoPedidoService.sincronizar(pedido)

        entrega = romaneio.itens.get()
        self.assertEqual(entrega.pedido_expedicao, pedido)
        self.assertEqual(entrega.cliente_nome, str(self.cliente))
        self.assertEqual(entrega.endereco_entrega['endereco'], 'Rua Áustria')
        self.assertEqual(entrega.volumes, Decimal('5'))
        self.assertEqual(entrega.peso_kg, Decimal('2.000'))
        self.assertEqual(entrega.valor, Decimal('34.00'))

    def test_sincronizar_duas_vezes_nao_duplica_a_parada(self):
        """O motorista sairia com a carga dobrada no papel."""
        romaneio = self._romaneio()
        pedido = self._pedido_com_carga(romaneio)

        RomaneioDoPedidoService.sincronizar(pedido)
        RomaneioDoPedidoService.sincronizar(pedido)

        self.assertEqual(romaneio.itens.count(), 1)

    def test_o_romaneio_soma_a_carga_do_pedido(self):
        romaneio = self._romaneio()
        pedido = self._pedido_com_carga(romaneio)

        RomaneioDoPedidoService.sincronizar(pedido)

        romaneio.refresh_from_db()
        self.assertEqual(romaneio.valor_total, Decimal('34.00'))
        self.assertEqual(romaneio.peso_total_kg, Decimal('2.000'))

    def test_tirar_do_romaneio_tira_a_entrega(self):
        """
        Entrega órfã é carga que o motorista leva sem ter o que entregar — e
        o romaneio somando peso que não está no caminhão.
        """
        romaneio = self._romaneio()
        pedido = self._pedido_com_carga(romaneio)
        RomaneioDoPedidoService.sincronizar(pedido)

        pedido.romaneio = None
        pedido.save(update_fields=['romaneio'])
        RomaneioDoPedidoService.sincronizar(pedido)

        self.assertEqual(romaneio.itens.count(), 0)
        romaneio.refresh_from_db()
        self.assertEqual(romaneio.valor_total, Decimal('0'))

    def test_trocar_de_romaneio_move_a_entrega(self):
        primeiro = self._romaneio(numero=1)
        segundo = self._romaneio(numero=2)
        pedido = self._pedido_com_carga(primeiro)
        RomaneioDoPedidoService.sincronizar(pedido)

        pedido.romaneio = segundo
        pedido.save(update_fields=['romaneio'])
        RomaneioDoPedidoService.sincronizar(pedido)

        self.assertEqual(primeiro.itens.count(), 0)
        self.assertEqual(segundo.itens.count(), 1)

    def test_entrega_ja_feita_nao_e_apagada(self):
        """
        Apagar o registro de uma entrega feita é apagar a prova de que ela
        aconteceu. O vínculo se desfaz; a linha fica.
        """
        romaneio = self._romaneio()
        pedido = self._pedido_com_carga(romaneio)
        RomaneioDoPedidoService.sincronizar(pedido)
        entrega = romaneio.itens.get()
        entrega.status_entrega = ItemRomaneioCarga.StatusEntrega.ENTREGUE
        entrega.save(update_fields=['status_entrega'])

        pedido.romaneio = None
        pedido.save(update_fields=['romaneio'])
        RomaneioDoPedidoService.sincronizar(pedido)

        entrega.refresh_from_db()
        self.assertEqual(romaneio.itens.count(), 1)
        self.assertIsNone(entrega.pedido_expedicao)

    def test_salvar_o_pedido_pela_tela_cria_a_entrega(self):
        romaneio = self._romaneio()
        pedido = self._pedido_com_carga()

        self.client.post(
            reverse('logistica:pedido-expedicao-update', args=[pedido.pk]),
            {
                'numero': pedido.numero,
                'data_pedido': pedido.data_pedido.isoformat(),
                'status': pedido.status,
                'prioridade': pedido.prioridade,
                'cliente': self.cliente.pk,
                'romaneio': romaneio.pk,
            },
            follow=True,
        )

        self.assertEqual(romaneio.itens.count(), 1)


class CobrancaDaExpedicaoTests(ItensDaVendaBase):
    """A carga que não nasce de venda também precisa virar dinheiro."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # A prazo abre titulo; dinheiro na entrega, nao.
        cls.a_prazo = FormaPagamento.objects.create(
            empresa=cls.empresa, descricao='Boleto 30 dias', tipo='boleto',
            gera_parcelas=True, prazo_liquidacao_dias=30,
        )
        cls.a_vista = FormaPagamento.objects.create(
            empresa=cls.empresa, descricao='Dinheiro na entrega',
            tipo='dinheiro', gera_parcelas=False,
        )
        cls.condicao = CondicaoPagamento.objects.create(
            empresa=cls.empresa, descricao='3x 30/60/90',
            numero_parcelas=3, intervalo_dias=30, dias_primeira_parcela=30,
        )

    def _carga_avulsa(self, forma=None, condicao=None):
        pedido = self._expedicao()
        pedido.forma_pagamento = forma if forma is not None else self.a_prazo
        pedido.condicao_pagamento = condicao if condicao is not None else self.condicao
        pedido.save()
        pedido.itens.create(
            ordem=1, produto_nome='Tangerina 400 g', quantidade=Decimal('1'),
            volumes=Decimal('5'), valor_unitario=Decimal('6.80'),
        )
        pedido.recalcular_totais()
        pedido.refresh_from_db()
        return pedido

    def test_carga_avulsa_a_prazo_abre_contas_a_receber(self):
        pedido = self._carga_avulsa()

        titulos = FinanceiroExpedicaoService.gerar_titulos(pedido, self.usuario)

        self.assertEqual(len(titulos), 3)
        self.assertEqual(
            sum(t.valor_final for t in titulos), Decimal('34.00'),
        )
        self.assertEqual(titulos[0].cliente, self.cliente)
        self.assertEqual(titulos[0].documento_tipo, 'pedido_expedicao')
        self.assertEqual(titulos[0].filial, self.filial)

    def test_expedicao_de_venda_nao_cobra_de_novo(self):
        """
        Um segundo título sobre a mesma mercadoria é o cliente pagando duas
        vezes — e o erro aparece na conciliação, semanas depois.
        """
        venda = self._venda_com_itens('10')
        pedido = self._carga_avulsa()
        pedido.pedido_venda = venda
        pedido.save(update_fields=['pedido_venda'])

        with self.assertRaises(DadosInvalidosError) as erro:
            FinanceiroExpedicaoService.gerar_titulos(pedido, self.usuario)

        self.assertIn('quem cobra é a venda', str(erro.exception))

    def test_cobrar_duas_vezes_nao_duplica(self):
        pedido = self._carga_avulsa()
        FinanceiroExpedicaoService.gerar_titulos(pedido, self.usuario)

        with self.assertRaises(DadosInvalidosError):
            FinanceiroExpedicaoService.gerar_titulos(pedido, self.usuario)

        self.assertEqual(FinanceiroExpedicaoService.titulos(pedido).count(), 3)

    def test_dinheiro_na_entrega_nao_abre_titulo(self):
        pedido = self._carga_avulsa(forma=self.a_vista, condicao=None)

        with self.assertRaises(DadosInvalidosError) as erro:
            FinanceiroExpedicaoService.gerar_titulos(pedido, self.usuario)

        self.assertIn('recebimento na entrega', str(erro.exception))

    def test_carga_sem_valor_nao_cobra(self):
        pedido = self._expedicao()
        pedido.forma_pagamento = self.a_prazo
        pedido.save(update_fields=['forma_pagamento'])

        with self.assertRaises(DadosInvalidosError) as erro:
            FinanceiroExpedicaoService.gerar_titulos(pedido, self.usuario)

        self.assertIn('sem valor', str(erro.exception))

    def test_sem_forma_escolhida_a_tela_diz_o_que_falta(self):
        pedido = self._carga_avulsa(forma=None, condicao=None)
        pedido.forma_pagamento = None
        pedido.save(update_fields=['forma_pagamento'])

        resumo = FinanceiroExpedicaoService.resumo(pedido)

        self.assertFalse(resumo['pode_cobrar'])
        self.assertIn('forma de pagamento', resumo['impedimento'])

    def test_cobrar_pela_tela(self):
        pedido = self._carga_avulsa()

        self.client.post(
            reverse('logistica:pedido-expedicao-cobrar', args=[pedido.pk]),
            follow=True,
        )

        self.assertEqual(FinanceiroExpedicaoService.titulos(pedido).count(), 3)

    def test_a_tela_diz_quando_quem_cobra_e_a_venda(self):
        venda = self._venda_com_itens('10')
        pedido = self._carga_avulsa()
        pedido.pedido_venda = venda
        pedido.save(update_fields=['pedido_venda'])

        html = self.client.get(
            reverse('logistica:pedido-expedicao-detail', args=[pedido.pk]),
        ).content.decode()

        self.assertIn('Cobrada pelo pedido de venda', html)
        self.assertNotIn('gerar cobrança', html)

    def test_a_tela_oferece_a_cobranca_da_carga_avulsa(self):
        pedido = self._carga_avulsa()

        html = self.client.get(
            reverse('logistica:pedido-expedicao-detail', args=[pedido.pk]),
        ).content.decode()

        # AS DUAS PERGUNTAS NA MESMA TELA: quanto se cobra e quando o
        # dinheiro entra.
        self.assertIn('gerar cobrança', html)
        self.assertIn('pagamento antecipado', html)


class EscolhaDaFormaNaTelaTests(CobrancaDaExpedicaoTests):
    """
    A forma se escolhe na própria tela do pedido, como no PDV.

    ELA VIVIA NO FORMULÁRIO DE EDIÇÃO, e quem terminava o pedido teria de
    voltar para outra tela só para dizer como cobrar — e não voltava. A carga
    saía sem cobrança nenhuma.
    """

    def test_a_tela_lista_as_formas_do_cadastro(self):
        pedido = self._carga_avulsa(forma=None, condicao=None)
        pedido.forma_pagamento = None
        pedido.save(update_fields=['forma_pagamento'])

        html = self.client.get(
            reverse('logistica:pedido-expedicao-detail', args=[pedido.pk]),
        ).content.decode()

        self.assertIn('Boleto 30 dias', html)
        self.assertIn('Dinheiro na entrega', html)
        self.assertIn('Escolha como esta carga é paga', html)

    def test_forma_nova_no_cadastro_aparece_sozinha(self):
        """Nenhuma lista fixa na tela: o cadastro manda."""
        FormaPagamento.objects.create(
            empresa=self.empresa, descricao='Pix 7 dias', tipo='pix',
            gera_parcelas=True, prazo_liquidacao_dias=7,
        )
        pedido = self._carga_avulsa()

        html = self.client.get(
            reverse('logistica:pedido-expedicao-detail', args=[pedido.pk]),
        ).content.decode()

        self.assertIn('Pix 7 dias', html)

    def test_forma_desativada_some_da_tela(self):
        self.a_vista.ativo = False
        self.a_vista.save(update_fields=['ativo'])
        pedido = self._carga_avulsa()

        html = self.client.get(
            reverse('logistica:pedido-expedicao-detail', args=[pedido.pk]),
        ).content.decode()

        self.assertNotIn('Dinheiro na entrega', html)

    def test_escolher_a_forma_na_tela_cobra_e_fica_registrado(self):
        pedido = self._carga_avulsa(forma=None, condicao=None)
        pedido.forma_pagamento = None
        pedido.condicao_pagamento = None
        pedido.save(update_fields=['forma_pagamento', 'condicao_pagamento'])

        self.client.post(
            reverse('logistica:pedido-expedicao-cobrar', args=[pedido.pk]),
            {
                'forma_pagamento': self.a_prazo.pk,
                'condicao_pagamento': self.condicao.pk,
            },
            follow=True,
        )

        pedido.refresh_from_db()
        # COMO A CARGA FOI COBRADA fica no pedido: sem isso o titulo existiria
        # e ninguem saberia por qual acerto ele saiu.
        self.assertEqual(pedido.forma_pagamento, self.a_prazo)
        self.assertEqual(pedido.condicao_pagamento, self.condicao)
        self.assertEqual(FinanceiroExpedicaoService.titulos(pedido).count(), 3)

    def test_sem_condicao_escolhida_sai_em_uma_parcela(self):
        pedido = self._carga_avulsa(forma=None, condicao=None)
        pedido.forma_pagamento = None
        pedido.condicao_pagamento = None
        pedido.save(update_fields=['forma_pagamento', 'condicao_pagamento'])

        self.client.post(
            reverse('logistica:pedido-expedicao-cobrar', args=[pedido.pk]),
            {'forma_pagamento': self.a_prazo.pk, 'condicao_pagamento': ''},
            follow=True,
        )

        titulos = list(FinanceiroExpedicaoService.titulos(pedido))
        self.assertEqual(len(titulos), 1)
        self.assertEqual(titulos[0].valor_final, Decimal('34.00'))

    def test_forma_de_outra_empresa_e_ignorada(self):
        """
        Um id vindo do POST pode ser de qualquer lugar, e cobrar com a forma
        de outra empresa poria o título na conta errada.
        """
        outra_empresa = Empresa.objects.create(
            razao_social='Alheia LTDA', nome_fantasia='Alheia',
            cnpj='55345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        alheia = FormaPagamento.objects.create(
            empresa=outra_empresa, descricao='Boleto alheio', tipo='boleto',
            gera_parcelas=True,
        )
        pedido = self._carga_avulsa()

        self.client.post(
            reverse('logistica:pedido-expedicao-cobrar', args=[pedido.pk]),
            {'forma_pagamento': alheia.pk},
            follow=True,
        )

        pedido.refresh_from_db()
        self.assertEqual(pedido.forma_pagamento, self.a_prazo)

    def test_dinheiro_na_entrega_nao_oferece_o_botao(self):
        """Recebimento na entrega não abre conta a receber — e a tela diz."""
        pedido = self._carga_avulsa(forma=self.a_vista, condicao=None)

        html = self.client.get(
            reverse('logistica:pedido-expedicao-detail', args=[pedido.pk]),
        ).content.decode()

        self.assertIn('Recebimento na entrega', html)


class PagamentoAntecipadoTests(CobrancaDaExpedicaoTests):
    """
    O cliente pagou antes de a carga sair.

    UM TÍTULO EM ABERTO FARIA A COBRANÇA PERSEGUIR QUEM JÁ PAGOU — e o
    dinheiro que entrou não apareceria em lugar nenhum. Antecipado nasce
    recebido.
    """

    def test_antecipado_nasce_recebido(self):
        pedido = self._carga_avulsa()

        titulos = FinanceiroExpedicaoService.gerar_titulos(
            pedido, self.usuario, antecipado=True,
        )

        self.assertEqual(len(titulos), 1)
        titulo = titulos[0]
        titulo.refresh_from_db()
        self.assertEqual(titulo.status, StatusContaReceber.PAGO)
        self.assertEqual(titulo.valor_pago, Decimal('34.00'))
        self.assertEqual(titulo.valor_saldo, Decimal('0.00'))
        self.assertEqual(titulo.data_pagamento, timezone.localdate())

    def test_o_adiantamento_nao_se_parcela(self):
        """Adiantamento parcelado é contradição: ou entrou, ou não entrou."""
        pedido = self._carga_avulsa()

        titulos = FinanceiroExpedicaoService.gerar_titulos(
            pedido, self.usuario, antecipado=True,
        )

        self.assertEqual(len(titulos), 1)
        self.assertEqual(titulos[0].valor_final, pedido.valor_total)

    def test_o_vencimento_e_hoje(self):
        """
        Data futura num título já recebido faria o fluxo de caixa esperar
        dinheiro que já entrou.
        """
        pedido = self._carga_avulsa()

        titulo = FinanceiroExpedicaoService.gerar_titulos(
            pedido, self.usuario, antecipado=True,
        )[0]

        self.assertEqual(titulo.data_vencimento, timezone.localdate())

    def test_forma_a_vista_tambem_aceita_antecipado(self):
        """
        Pix, dinheiro e cartão recebem adiantado o tempo todo — a forma não
        gerar parcelas não diz nada sobre quando o dinheiro entrou.
        """
        pedido = self._carga_avulsa(forma=self.a_vista, condicao=None)

        titulos = FinanceiroExpedicaoService.gerar_titulos(
            pedido, self.usuario, antecipado=True,
        )

        self.assertEqual(len(titulos), 1)
        titulos[0].refresh_from_db()
        self.assertEqual(titulos[0].status, StatusContaReceber.PAGO)

    def test_o_dinheiro_recebido_aparece_no_resumo(self):
        pedido = self._carga_avulsa()
        FinanceiroExpedicaoService.gerar_titulos(
            pedido, self.usuario, antecipado=True,
        )

        resumo = FinanceiroExpedicaoService.resumo(pedido)

        self.assertEqual(resumo['valor'], Decimal('34.00'))
        self.assertEqual(resumo['aberto'], Decimal('0.00'))

    def test_expedicao_de_venda_nao_recebe_adiantamento_aqui(self):
        """Quem cobra continua sendo a venda."""
        venda = self._venda_com_itens('10')
        pedido = self._carga_avulsa()
        pedido.pedido_venda = venda
        pedido.save(update_fields=['pedido_venda'])

        with self.assertRaises(DadosInvalidosError):
            FinanceiroExpedicaoService.gerar_titulos(
                pedido, self.usuario, antecipado=True,
            )

    def test_registrar_antecipado_pela_tela(self):
        pedido = self._carga_avulsa(forma=self.a_vista, condicao=None)

        self.client.post(
            reverse('logistica:pedido-expedicao-cobrar', args=[pedido.pk]),
            {'forma_pagamento': self.a_vista.pk, 'quando': 'antecipado'},
            follow=True,
        )

        titulo = FinanceiroExpedicaoService.titulos(pedido).get()
        self.assertEqual(titulo.status, StatusContaReceber.PAGO)

    def test_a_tela_oferece_o_antecipado_mesmo_na_forma_a_vista(self):
        pedido = self._carga_avulsa(forma=self.a_vista, condicao=None)

        html = self.client.get(
            reverse('logistica:pedido-expedicao-detail', args=[pedido.pk]),
        ).content.decode()

        self.assertIn('pagamento antecipado', html)

    def test_carga_ja_paga_nao_cobra_de_novo(self):
        pedido = self._carga_avulsa()
        FinanceiroExpedicaoService.gerar_titulos(
            pedido, self.usuario, antecipado=True,
        )

        with self.assertRaises(DadosInvalidosError) as erro:
            FinanceiroExpedicaoService.gerar_titulos(pedido, self.usuario)

        self.assertIn('já tem cobrança lançada', str(erro.exception))
