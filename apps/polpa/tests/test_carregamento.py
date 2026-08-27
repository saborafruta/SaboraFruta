"""
O carregamento: conferir a carga e medir o baú antes de fechar a porta.

O QUE ESTES TESTES CERCAM:

  · O DOCUMENTO DA CARGA É O DO ERP. `RomaneioCarga` já tem motorista,
    placa, paradas e status; aqui mora só a ficha fria, com o dado que não
    vale para todo mundo — quem entrega parafuso não mede o baú. Um
    romaneio próprio deste vertical daria dois documentos para o mesmo
    caminhão;

  · A TEMPERATURA EXIGIDA É DEDUZIDA, não digitada: a carga responde pela
    MAIS EXIGENTE que está dentro dela. Pedir para o conferente lembrar
    disso é pedir para errar num dia corrido;

  · CONFERIR NÃO É DESPACHAR. Um botão só para as duas coisas faria uma
    conferência interrompida virar caminhão despachado pela metade;

  · O QUE FALTA AVISA, NÃO TRAVA — menos a medição, que some para sempre
    quando o caminhão sai;

  · SEM MEDIÇÃO É `None`, e a tela diz isso. Zero seria um baú a 0°C — uma
    afirmação, e a pior possível, porque parece plausível.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.logistica.models import ItemRomaneioCarga, RomaneioCarga
from apps.polpa.models import CargaFria, FichaProduto
from apps.polpa.services import CatalogoService
from apps.polpa.services.carregamento import CarregamentoService
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial
from apps.vendas.models import ItemPedidoVenda, PedidoVenda

T = FichaProduto.Tipo
SE = ItemRomaneioCarga.StatusEntrega


class CarregamentoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Doca LTDA', nome_fantasia='Doca',
            cnpj='63345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='63345678000272',
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
            email='doca@carga.local', nome='Doca', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    # ── Fixtures ─────────────────────────────────────────────────────────

    def _produto(self, codigo='PM1', temperatura=Decimal('-18')):
        produto = CatalogoService.salvar(self.filial, {
            'tipo': T.POLPA, 'descricao': f'Polpa {codigo}', 'codigo': codigo,
            'unidade_medida': self.unidade, 'validade_dias': 180,
        }).produto
        produto.temperatura_maxima = temperatura
        produto.save(update_fields=['temperatura_maxima'])
        return produto

    def _pedido(self, produto):
        pedido = PedidoVenda.objects.create(
            filial=self.filial,
            numero_pedido=f'PV{PedidoVenda.objects.count() + 1}',
            cliente=self.cliente, usuario=self.usuario,
            status=PedidoVenda.Status.CONFIRMADO,
            data_emissao=timezone.now(),
        )
        ItemPedidoVenda.objects.create(
            pedido=pedido, produto=produto, quantidade=Decimal('10'),
            valor_unitario=Decimal('1'), valor_bruto=Decimal('10'),
            valor_total=Decimal('10'),
        )
        return pedido

    def _romaneio(self, pedidos=(), status=None, placa='NOP1A23'):
        romaneio = RomaneioCarga.objects.create(
            filial=self.filial, numero=RomaneioCarga.objects.count() + 1,
            status=status or RomaneioCarga.Status.EM_CARREGAMENTO,
            veiculo_placa=placa, motorista_nome='Seu Zé',
        )
        for i, pedido in enumerate(pedidos, start=1):
            ItemRomaneioCarga.objects.create(
                romaneio=romaneio, ordem=i, pedido_venda=pedido,
                cliente_nome=pedido.cliente.razao_social,
                peso_kg=Decimal('10'), volumes=Decimal('1'),
            )
        return romaneio


class FichaTests(CarregamentoBase):
    """A ficha fria — o dado que o romaneio não tinha."""

    def test_a_ficha_nasce_na_primeira_olhada_e_nao_se_duplica(self):
        romaneio = self._romaneio()

        primeira = CarregamentoService.ficha(romaneio)
        segunda = CarregamentoService.ficha(romaneio)

        self.assertEqual(primeira.pk, segunda.pk)
        self.assertEqual(CargaFria.objects.count(), 1)

    def test_sem_medicao_e_none_e_nao_zero(self):
        """Zero seria um baú a 0°C — uma afirmação, e das piores."""
        romaneio = self._romaneio()

        ficha = CarregamentoService.ficha(romaneio)

        self.assertIsNone(ficha.temperatura_bau)
        self.assertFalse(ficha.medida)

    def test_a_medicao_guarda_quem_mediu_e_quando(self):
        """A fiscalização pergunta as três coisas juntas."""
        romaneio = self._romaneio()

        ficha = CarregamentoService.registrar_temperatura(
            romaneio, Decimal('-19'), self.usuario,
        )

        self.assertEqual(ficha.temperatura_bau, Decimal('-19.00'))
        self.assertEqual(ficha.medido_por, self.usuario)
        self.assertIsNotNone(ficha.medida_em)

    def test_medir_sem_numero_e_recusado(self):
        romaneio = self._romaneio()

        with self.assertRaises(DomainError):
            CarregamentoService.registrar_temperatura(romaneio, None, self.usuario)


class ExigenciaTests(CarregamentoBase):
    """A temperatura que a carga exige sai do cadastro dos produtos."""

    def test_a_carga_responde_pelo_produto_mais_exigente(self):
        """
        Um baú a -12°C serve para o creme e estraga a polpa — e lembrar
        disso não pode ser tarefa de quem está com a prancheta na mão.
        """
        creme = self._pedido(self._produto('CR1', Decimal('-12')))
        polpa = self._pedido(self._produto('PM2', Decimal('-18')))
        romaneio = self._romaneio([creme, polpa])

        self.assertEqual(
            CarregamentoService.temperatura_exigida(romaneio), Decimal('-18.00'),
        )

    def test_sem_cadastro_nao_se_inventa_limite(self):
        pedido = self._pedido(self._produto('SEM', temperatura=None))
        romaneio = self._romaneio([pedido])

        self.assertIsNone(CarregamentoService.temperatura_exigida(romaneio))

    def test_bau_acima_do_exigido_e_marcado(self):
        pedido = self._pedido(self._produto())
        romaneio = self._romaneio([pedido])
        CarregamentoService.registrar_temperatura(
            romaneio, Decimal('-8'), self.usuario,
        )

        dados = CarregamentoService.carga(romaneio)

        self.assertTrue(dados['fora_da_faixa'])

    def test_bau_dentro_do_exigido_nao_e_alarme(self):
        pedido = self._pedido(self._produto())
        romaneio = self._romaneio([pedido])
        CarregamentoService.registrar_temperatura(
            romaneio, Decimal('-20'), self.usuario,
        )

        self.assertFalse(CarregamentoService.carga(romaneio)['fora_da_faixa'])

    def test_sem_medicao_nao_vira_alarme(self):
        """`None` é medição que faltou — a pendência diz isso, o alarme não."""
        pedido = self._pedido(self._produto())
        romaneio = self._romaneio([pedido])

        dados = CarregamentoService.carga(romaneio)

        self.assertFalse(dados['fora_da_faixa'])
        self.assertIn('Baú ainda não medido.', dados['pendencias'])


class ConferenciaTests(CarregamentoBase):
    """Marcar o que subiu no caminhão."""

    def test_marcar_e_desmarcar_uma_parada(self):
        """Errar a linha é o engano mais comum da doca."""
        pedido = self._pedido(self._produto())
        romaneio = self._romaneio([pedido])
        item = romaneio.itens.first()

        CarregamentoService.conferir(item)
        item.refresh_from_db()
        self.assertEqual(item.status_entrega, SE.CARREGADO)

        CarregamentoService.conferir(item, carregada=False)
        item.refresh_from_db()
        self.assertEqual(item.status_entrega, SE.PENDENTE)

    def test_parada_pendente_vira_aviso_e_nao_trava(self):
        pedido = self._pedido(self._produto())
        romaneio = self._romaneio([pedido])

        dados = CarregamentoService.carga(romaneio)

        self.assertTrue(any('não conferida' in p for p in dados['pendencias']))
        # E ainda assim a carga pode sair: o aviso é informação, não cadeado.
        CarregamentoService.despachar(romaneio, Decimal('-19'), self.usuario)
        romaneio.refresh_from_db()
        self.assertEqual(romaneio.status, RomaneioCarga.Status.EM_ROTA)

    def test_pedido_sem_separacao_e_avisado(self):
        """Sem separação, num recall ninguém sabe o que foi para quem."""
        pedido = self._pedido(self._produto())
        romaneio = self._romaneio([pedido])

        dados = CarregamentoService.carga(romaneio)

        self.assertTrue(dados['paradas'][0]['sem_separacao'])
        self.assertTrue(
            any('sem separação' in p for p in dados['pendencias'])
        )


class DespachoTests(CarregamentoBase):
    """Fechar a porta."""

    def test_despachar_grava_temperatura_hora_e_poe_em_rota(self):
        pedido = self._pedido(self._produto())
        romaneio = self._romaneio([pedido])

        ficha = CarregamentoService.despachar(
            romaneio, Decimal('-19'), self.usuario,
        )

        romaneio.refresh_from_db()
        self.assertEqual(romaneio.status, RomaneioCarga.Status.EM_ROTA)
        self.assertEqual(ficha.temperatura_bau, Decimal('-19.00'))
        self.assertIsNotNone(ficha.saida_em)
        self.assertTrue(ficha.despachada)

    def test_despachar_sem_temperatura_e_recusado(self):
        """
        A medição some para sempre quando o caminhão sai, e custa dez
        segundos de termômetro. Não é trava: é a pergunta feita na hora em
        que ela ainda é barata.
        """
        romaneio = self._romaneio()

        with self.assertRaises(DomainError):
            CarregamentoService.despachar(romaneio, None, self.usuario)

        romaneio.refresh_from_db()
        self.assertEqual(romaneio.status, RomaneioCarga.Status.EM_CARREGAMENTO)

    def test_carga_que_ja_saiu_nao_sai_de_novo(self):
        romaneio = self._romaneio()
        CarregamentoService.despachar(romaneio, Decimal('-19'), self.usuario)

        with self.assertRaises(DomainError):
            CarregamentoService.despachar(romaneio, Decimal('-19'), self.usuario)


class ListaTests(CarregamentoBase):
    """A fila da doca."""

    def test_a_doca_vem_antes_do_que_ja_saiu(self):
        saiu = self._romaneio()
        CarregamentoService.despachar(saiu, Decimal('-19'), self.usuario)
        na_doca = self._romaneio()

        linhas = CarregamentoService.cargas(self.filial)

        self.assertEqual(
            [l['romaneio'] for l in linhas], [na_doca, saiu],
        )

    def test_carga_entregue_nao_aparece(self):
        """Depois da entrega o assunto é outro — e outra tela."""
        self._romaneio(status=RomaneioCarga.Status.ENTREGUE)

        self.assertEqual(CarregamentoService.cargas(self.filial), [])

    def test_a_lista_conta_paradas_conferidas(self):
        pedido = self._pedido(self._produto())
        romaneio = self._romaneio([pedido, self._pedido(self._produto('PM3'))])
        CarregamentoService.conferir(romaneio.itens.first())

        linha = CarregamentoService.cargas(self.filial)[0]

        self.assertEqual((linha['carregadas'], linha['paradas']), (1, 2))


class TelaTests(CarregamentoBase):
    """As duas telas."""

    def test_a_lista_abre(self):
        romaneio = self._romaneio()

        resposta = self.client.get(reverse('polpa:carregamento'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, romaneio.veiculo_placa)

    def test_a_lista_nao_e_o_placeholder_em_construcao(self):
        resposta = self.client.get(
            reverse('polpa:item', args=['expedicao', 'carregamento']),
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, 'Tela em construção')

    def test_a_carga_abre_com_a_exigencia(self):
        pedido = self._pedido(self._produto())
        romaneio = self._romaneio([pedido])

        resposta = self.client.get(
            reverse('polpa:carregamento-carga', args=[romaneio.pk]),
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Mercado Central')

    def test_conferir_pela_tela(self):
        pedido = self._pedido(self._produto())
        romaneio = self._romaneio([pedido])
        item = romaneio.itens.first()

        self.client.post(
            reverse('polpa:carregamento-carga', args=[romaneio.pk]),
            {'acao': 'conferir', 'item': item.pk, 'carregada': '1'},
        )

        item.refresh_from_db()
        self.assertEqual(item.status_entrega, SE.CARREGADO)

    def test_despachar_pela_tela_com_virgula(self):
        """A doca digita -18,5 — o ponto decimal é detalhe de teclado."""
        romaneio = self._romaneio()

        self.client.post(
            reverse('polpa:carregamento-carga', args=[romaneio.pk]),
            {'acao': 'despachar', 'temperatura': '-18,5'},
        )

        romaneio.refresh_from_db()
        self.assertEqual(romaneio.status, RomaneioCarga.Status.EM_ROTA)
        self.assertEqual(
            romaneio.ficha_polpa.temperatura_bau, Decimal('-18.50'),
        )

    def test_a_tela_explica_a_recusa_em_vez_de_estourar(self):
        romaneio = self._romaneio()

        resposta = self.client.post(
            reverse('polpa:carregamento-carga', args=[romaneio.pk]),
            {'acao': 'despachar', 'temperatura': ''}, follow=True,
        )

        self.assertEqual(resposta.status_code, 200)
        romaneio.refresh_from_db()
        self.assertEqual(romaneio.status, RomaneioCarga.Status.EM_CARREGAMENTO)
        self.assertContains(resposta, 'Informe a temperatura')


class MontarCargaPelasVendasTests(CarregamentoBase):
    """
    A carga começa na doca, e não na Logística.

    Quem está com o caminhão encostado não vai a outro módulo pedir que montem
    o romaneio — ele carrega e anota depois, que é como a expedição deixa de
    ter registro.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse('polpa:carregamento')
        self.acabado = self._produto('PM1')
        # Materia-prima existe so' para provar que ela NAO entra na doca: o
        # vertical carrega o que fabrica, e fruta in natura nao sai da camara
        # como produto.
        self.materia_prima = CatalogoService.salvar(self.filial, {
            'tipo': T.FRUTA, 'descricao': 'Manga in natura', 'codigo': 'MP1',
            'unidade_medida': self.unidade,
        }).produto

    # ── A lista de vendas ────────────────────────────────────────────────

    def test_pedido_confirmado_aparece_para_carregar(self):
        self._pedido(self.acabado)

        resposta = self.client.get(self.url)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(resposta.context['vendas']), 1)

    def test_pedido_ja_em_carga_nao_aparece_de_novo(self):
        """
        E a regra que impede a mesma mercadoria de ser carregada em dois
        caminhoes.
        """
        pedido = self._pedido(self.acabado)
        self._romaneio(pedidos=[pedido])

        resposta = self.client.get(self.url)

        self.assertEqual(len(resposta.context['vendas']), 0)

    def test_carga_cancelada_devolve_o_pedido_para_a_lista(self):
        """Aquela carga deixou de existir; a mercadoria voltou a esperar."""
        pedido = self._pedido(self.acabado)
        self._romaneio(pedidos=[pedido], status=RomaneioCarga.Status.CANCELADO)

        resposta = self.client.get(self.url)

        self.assertEqual(len(resposta.context['vendas']), 1)

    def test_pedido_de_produto_que_a_fabrica_nao_faz_fica_de_fora(self):
        """
        Pedido de item que este vertical nao fabrica nao sai desta camara, e
        enfileira-lo aqui faria a doca prometer o que ela nao tem.
        """
        self._pedido(self.materia_prima)

        resposta = self.client.get(self.url)

        self.assertEqual(len(resposta.context['vendas']), 0)

    def test_a_lista_vem_pela_data_de_entrega(self):
        """
        Quem carrega trabalha contra a data em que o caminhao precisa sair.
        Pedido sem data vai para o fim e nao some -- sumir e como ele atrasa.
        """
        sem_data = self._pedido(self.acabado)
        urgente = self._pedido(self.acabado)
        urgente.data_entrega_prevista = timezone.localdate()
        urgente.save(update_fields=['data_entrega_prevista'])

        vendas = self.client.get(self.url).context['vendas']

        self.assertEqual(vendas[0]['pedido'].pk, urgente.pk)
        self.assertEqual(vendas[-1]['pedido'].pk, sem_data.pk)

    # ── Montar ───────────────────────────────────────────────────────────

    def test_montar_carga_cria_o_romaneio_com_os_pedidos(self):
        primeiro = self._pedido(self.acabado)
        segundo = self._pedido(self.acabado)

        resposta = self.client.post(self.url, {
            'pedidos': [primeiro.pk, segundo.pk],
            'motorista_nome': 'Seu Zé', 'veiculo_placa': 'abc1d23',
            'destino_rota': 'Zona Norte',
        })

        romaneio = RomaneioCarga.objects.latest('id')
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(romaneio.itens.count(), 2)
        self.assertEqual(romaneio.status, RomaneioCarga.Status.EM_CARREGAMENTO)

    def test_a_placa_vai_em_maiuscula(self):
        pedido = self._pedido(self.acabado)

        self.client.post(self.url, {
            'pedidos': [pedido.pk], 'veiculo_placa': 'abc1d23',
        })

        self.assertEqual(RomaneioCarga.objects.latest('id').veiculo_placa, 'ABC1D23')

    def test_o_numero_e_gerado_e_nao_pedido(self):
        """
        Na doca, pedir um numero inventado e atrito puro -- e numero repetido
        bate na `unique_together` depois de a pessoa ja ter escolhido tudo.
        """
        self._romaneio()
        pedido = self._pedido(self.acabado)

        self.client.post(self.url, {
            'pedidos': [pedido.pk], 'veiculo_placa': 'XYZ1A23',
        })

        numeros = list(RomaneioCarga.objects.order_by('numero').values_list('numero', flat=True))
        self.assertEqual(len(numeros), len(set(numeros)))

    def test_sem_pedido_escolhido_nao_monta(self):
        resposta = self.client.post(self.url, {
            'pedidos': [], 'veiculo_placa': 'ABC1D23',
        }, follow=True)

        self.assertEqual(RomaneioCarga.objects.count(), 0)
        self.assertContains(resposta, 'ao menos uma venda')

    def test_sem_motorista_nem_placa_nao_monta(self):
        """A carga sairia sem dizer quem levou."""
        pedido = self._pedido(self.acabado)

        resposta = self.client.post(self.url, {
            'pedidos': [pedido.pk], 'motorista_nome': '', 'veiculo_placa': '  ',
        }, follow=True)

        self.assertEqual(RomaneioCarga.objects.count(), 0)
        self.assertContains(resposta, 'quem levou')

    def test_montar_leva_direto_para_a_conferencia(self):
        """
        Quem acabou de escolher os pedidos esta com o caminhao encostado, e o
        proximo passo e conferir a carga -- nao olhar a doca de novo.
        """
        pedido = self._pedido(self.acabado)

        resposta = self.client.post(self.url, {
            'pedidos': [pedido.pk], 'veiculo_placa': 'ABC1D23',
        })

        romaneio = RomaneioCarga.objects.latest('id')
        self.assertRedirects(
            resposta,
            reverse('polpa:carregamento-carga', args=[romaneio.pk]),
        )

    def test_o_endereco_e_copiado_e_nao_apontado(self):
        """
        E para onde o caminhao foi naquele dia. Cliente que muda de endereco
        depois nao pode reescrever o historico de uma entrega ja feita.
        """
        pedido = self._pedido(self.acabado)

        self.client.post(self.url, {
            'pedidos': [pedido.pk], 'veiculo_placa': 'ABC1D23',
        })

        item = RomaneioCarga.objects.latest('id').itens.first()
        self.assertIsInstance(item.endereco_entrega, dict)
        self.assertIn('cidade', item.endereco_entrega)

    def test_pedido_de_outra_filial_nao_entra_na_carga(self):
        """
        Id colado a mao montaria carga com pedido de outra unidade, e o
        caminhao sairia com mercadoria que nao e desta casa.
        """
        from apps.core.models import Filial

        outra = Filial.objects.create(
            empresa=self.empresa, razao_social='Segunda',
            cnpj='31345678000677', uf='RN', cidade='Mossoro',
        )
        alheio = self._pedido(self.acabado)
        alheio.filial = outra
        alheio.save(update_fields=['filial'])

        resposta = self.client.post(self.url, {
            'pedidos': [alheio.pk], 'veiculo_placa': 'ABC1D23',
        }, follow=True)

        self.assertEqual(RomaneioCarga.objects.count(), 0)
        self.assertContains(resposta, 'ao menos uma venda')

    def test_a_tela_nao_vaza_sintaxe_de_template(self):
        self._pedido(self.acabado)

        html = self.client.get(self.url).content.decode()

        for resto in ('{#', '#}', '{%', '%}'):
            self.assertNotIn(resto, html, 'vazou sintaxe de template no HTML')
