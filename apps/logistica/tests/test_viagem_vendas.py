"""
O seletor de vendas já realizadas.

ESCOLHE-SE A VENDA, NÃO O PRODUTO. Redigitar produto e quantidade para
mercadoria que já foi vendida é uma chance de a carga sair diferente do que o
cliente comprou.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import ItemCarga, Viagem
from apps.logistica.services.vendas_para_carga import VendasParaCargaService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial
from apps.vendas.models.pedido import ItemPedidoVenda, PedidoVenda


class SeletorDeVendasTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Vendas LTDA', nome_fantasia='Vendas',
            cnpj='63345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='63345678000272',
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
            email='sel@vendas.local', nome='Sel', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Cliente A',
            cpf_cnpj='12345678901', uf='RN',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)

        # PESO E VOLUME VEM DO PRODUTO: o item do pedido nao os guarda.
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade,
            descricao='Caixa de polpa', codigo='CX1', ncm='20079900',
            peso_bruto=Decimal('2.500'),
            largura=Decimal('30'), altura=Decimal('20'), profundidade=Decimal('40'),
        )
        ProdutoFilial.objects.create(produto=cls.produto, filial=cls.filial)

        natureza = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='venda', descricao='Venda',
            especie=NaturezaOperacao.Especie.VENDA,
        )
        RegraNaturezaOperacao.objects.create(natureza=natureza, cfop='5102')

    def setUp(self):
        self.client.force_login(self.usuario)
        self.viagem = Viagem.objects.create(
            filial=self.filial, numero=1, motorista_nome='Seu Zé',
            veiculo_placa='ABC1D23',
        )
        self.url = reverse('logistica:viagem-vendas', args=[self.viagem.pk])

    def _pedido(self, numero='PV1', quantidade='100',
                status=PedidoVenda.Status.CONFIRMADO, cliente=None):
        pedido = PedidoVenda.objects.create(
            filial=self.filial, numero_pedido=numero,
            cliente=cliente or self.cliente, usuario=self.usuario,
            status=status, data_emissao=timezone.now(),
        )
        ItemPedidoVenda.objects.create(
            pedido=pedido, produto=self.produto,
            quantidade=Decimal(quantidade), valor_unitario=Decimal('10'),
            valor_bruto=Decimal(quantidade) * 10, valor_total=Decimal(quantidade) * 10,
        )
        return pedido

    # ── A lista ──────────────────────────────────────────────────────────

    def test_a_tela_mostra_as_colunas_pedidas(self):
        self._pedido()

        html = self.client.get(self.url).content.decode()

        for coluna in ('Venda', 'Cliente', 'NF-e', 'Produto',
                       'Quantidade', 'Peso', 'Volume', 'Valor'):
            self.assertIn(coluna, html, f'a coluna {coluna} sumiu')

    def test_pedido_confirmado_aparece(self):
        self._pedido()

        vendas = VendasParaCargaService.disponiveis(self.filial)

        self.assertEqual(len(vendas), 1)

    def test_pedido_faturado_tambem_aparece(self):
        """
        Nada neste sistema marca um pedido como ENTREGUE: faturar é o fim da
        linha do lado comercial, e o pedido fica parado esperando quem o leve.
        """
        self._pedido(status=PedidoVenda.Status.FATURADO)

        self.assertEqual(len(VendasParaCargaService.disponiveis(self.filial)), 1)

    def test_rascunho_nao_aparece(self):
        """Carregar o que ainda pode mudar é prometer o que talvez não se cumpra."""
        self._pedido(status=PedidoVenda.Status.RASCUNHO)

        self.assertEqual(len(VendasParaCargaService.disponiveis(self.filial)), 0)

    def test_venda_ja_carregada_nao_aparece_de_novo(self):
        """É a regra que impede a mesma mercadoria de subir em dois caminhões."""
        pedido = self._pedido()
        VendasParaCargaService.adicionar_vendas(self.viagem, [pedido])

        self.assertEqual(len(VendasParaCargaService.disponiveis(self.filial)), 0)

    def test_viagem_cancelada_devolve_a_venda_para_a_lista(self):
        pedido = self._pedido()
        VendasParaCargaService.adicionar_vendas(self.viagem, [pedido])
        self.viagem.status = Viagem.Status.CANCELADA
        self.viagem.save(update_fields=['status'])

        self.assertEqual(len(VendasParaCargaService.disponiveis(self.filial)), 1)

    # ── Peso e volume ────────────────────────────────────────────────────

    def test_o_peso_vem_do_cadastro_do_produto(self):
        """O item do pedido guarda quantidade e valor, não peso."""
        self._pedido(quantidade='100')

        venda = VendasParaCargaService.disponiveis(self.filial)[0]

        self.assertEqual(venda['peso'], Decimal('250.000'))

    def test_o_volume_vem_das_dimensoes_do_produto(self):
        self._pedido(quantidade='100')

        venda = VendasParaCargaService.disponiveis(self.filial)[0]

        # 30 × 20 × 40 cm = 24000 cm³ = 0,024 m³ por caixa.
        self.assertEqual(venda['volume'], Decimal('2.400'))

    def test_produto_sem_peso_nao_trava_a_carga(self):
        """
        O peso serve para conferência e para o MDF-e; barrar o carregamento por
        cadastro incompleto pararia o caminhão por um problema de retaguarda.
        """
        sem_peso = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade,
            descricao='Sem peso', codigo='SP1', ncm='20079900',
        )
        ProdutoFilial.objects.create(produto=sem_peso, filial=self.filial)
        pedido = self._pedido(numero='PV9')
        ItemPedidoVenda.objects.create(
            pedido=pedido, produto=sem_peso, quantidade=Decimal('5'),
            valor_unitario=Decimal('1'), valor_bruto=Decimal('5'),
            valor_total=Decimal('5'),
        )

        criados = VendasParaCargaService.adicionar_vendas(self.viagem, [pedido])

        self.assertEqual(criados, 2)

    # ── A NF-e ───────────────────────────────────────────────────────────

    def test_sem_nfe_a_coluna_diz_isso_em_vez_de_ficar_vazia(self):
        """
        Nada neste sistema emite NF-e a partir de pedido de venda ainda.
        Mostrar em branco é honesto; inventar um número, não.
        """
        self._pedido()

        html = self.client.get(self.url).content.decode()

        self.assertIn('sem NF-e', html)

    # ── Carregar ─────────────────────────────────────────────────────────

    def test_marcar_a_venda_traz_os_itens_dela(self):
        pedido = self._pedido(quantidade='100')

        self.client.post(self.url, {'pedidos': [pedido.pk]}, follow=True)

        item = ItemCarga.objects.get(viagem=self.viagem)
        self.assertEqual(item.produto, self.produto)
        self.assertEqual(item.quantidade, Decimal('100.000'))
        self.assertEqual(item.cliente, self.cliente)
        self.assertEqual(item.pedido_venda, pedido)

    def test_varias_vendas_na_mesma_viagem(self):
        """É assim que a doca trabalha: escolhe-se tudo que vai no caminhão."""
        primeiro = self._pedido('PV1', '100')
        segundo = self._pedido('PV2', '50')

        self.client.post(
            self.url, {'pedidos': [primeiro.pk, segundo.pk]}, follow=True,
        )

        self.assertEqual(ItemCarga.objects.filter(viagem=self.viagem).count(), 2)

    def test_a_natureza_da_linha_e_de_venda(self):
        pedido = self._pedido()

        VendasParaCargaService.adicionar_vendas(self.viagem, [pedido])

        item = ItemCarga.objects.get(viagem=self.viagem)
        self.assertEqual(item.natureza.especie, NaturezaOperacao.Especie.VENDA)

    def test_o_peso_calculado_vai_para_a_linha_da_carga(self):
        pedido = self._pedido(quantidade='100')

        VendasParaCargaService.adicionar_vendas(self.viagem, [pedido])

        self.assertEqual(
            ItemCarga.objects.get(viagem=self.viagem).peso_kg, Decimal('250.000'),
        )

    def test_sem_venda_marcada_nao_carrega(self):
        resposta = self.client.post(self.url, {'pedidos': []}, follow=True)

        self.assertEqual(ItemCarga.objects.count(), 0)
        avisos = [str(m) for m in resposta.context['messages']]
        self.assertTrue(any('ao menos uma venda' in a for a in avisos), avisos)

    def test_venda_de_outra_filial_nao_carrega_nem_por_id(self):
        """
        Id colado à mão carregaria venda de outra unidade, e o caminhão sairia
        com mercadoria que não é desta casa.
        """
        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Segunda',
            cnpj='31345678000677', uf='RN', cidade='Mossoro',
        )
        alheio = PedidoVenda.objects.create(
            filial=outra, numero_pedido='PVX', cliente=self.cliente,
            usuario=self.usuario, status=PedidoVenda.Status.CONFIRMADO,
            data_emissao=timezone.now(),
        )

        resposta = self.client.post(self.url, {'pedidos': [alheio.pk]}, follow=True)

        self.assertEqual(ItemCarga.objects.count(), 0)
        avisos = [str(m) for m in resposta.context['messages']]
        self.assertTrue(any('ao menos uma venda' in a for a in avisos), avisos)

    def test_sem_natureza_de_venda_cadastrada_o_erro_diz_onde_cadastrar(self):
        NaturezaOperacao.objects.filter(
            especie=NaturezaOperacao.Especie.VENDA,
        ).delete()
        pedido = self._pedido()

        with self.assertRaises(DadosInvalidosError) as erro:
            VendasParaCargaService.adicionar_vendas(self.viagem, [pedido])

        self.assertIn('Naturezas de operação', str(erro.exception))

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        self._pedido()

        html = self.client.get(self.url).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, 'vazou sintaxe de template no HTML')
