"""
A entrega: o último elo da cadeia de frio.

O QUE ESTES TESTES CERCAM:

  · A PARADA JÁ EXISTIA, A PROVA NÃO. O status "entregue" é do romaneio; o
    canhoto — quem recebeu, quando, em que temperatura — mora na ficha
    deste vertical. Status sozinho é a fábrica dizendo que entregou, e não
    há o que pôr do outro lado da mesa quando o cliente reclama;

  · NOME É PROVA, "SIM" NÃO É. `recebido_por` é a única exigência para
    marcar entrega — um booleano transforma divergência em palavra contra
    palavra;

  · TEMPERATURA COBRADA, NÃO EXIGIDA. Recusar o registro por falta dela
    deixaria a entrega sem NENHUM canhoto, que é pior. A tela avisa; o
    registro acontece;

  · NÃO ENTREGUE PRECISA DE MOTIVO: sem razão vira número em relatório e
    some; com razão, vira roteiro que muda;

  · O ROMANEIO FECHA SOZINHO quando todas as paradas são resolvidas — um
    botão "encerrar" seria um segundo lugar para dizer a mesma coisa.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.logistica.models import ItemRomaneioCarga, RomaneioCarga
from apps.polpa.models import EntregaFria, FichaProduto
from apps.polpa.services import CatalogoService
from apps.polpa.services.carregamento import CarregamentoService
from apps.polpa.services.entrega import EntregaService
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial
from apps.vendas.models import ItemPedidoVenda, PedidoVenda

T = FichaProduto.Tipo
SE = ItemRomaneioCarga.StatusEntrega
OC = EntregaFria.Ocorrencia


class EntregaBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Entrega LTDA', nome_fantasia='Entrega',
            cnpj='73145678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='73145678000272',
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
            email='rua@entrega.local', nome='Rua', password='x' * 12,
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

    def _pedido(self, produto=None):
        pedido = PedidoVenda.objects.create(
            filial=self.filial,
            numero_pedido=f'PV{PedidoVenda.objects.count() + 1}',
            cliente=self.cliente, usuario=self.usuario,
            status=PedidoVenda.Status.CONFIRMADO,
            data_emissao=timezone.now(),
        )
        ItemPedidoVenda.objects.create(
            pedido=pedido, produto=produto or self._produto(), quantidade=Decimal('10'),
            valor_unitario=Decimal('1'), valor_bruto=Decimal('10'),
            valor_total=Decimal('10'),
        )
        return pedido

    def _carga(self, paradas=1, despachar=True, temperatura=Decimal('-19')):
        romaneio = RomaneioCarga.objects.create(
            filial=self.filial, numero=RomaneioCarga.objects.count() + 1,
            status=RomaneioCarga.Status.EM_CARREGAMENTO,
            veiculo_placa='NOP1A23', motorista_nome='Seu Zé',
        )
        for i in range(paradas):
            pedido = self._pedido()
            ItemRomaneioCarga.objects.create(
                romaneio=romaneio, ordem=i + 1, pedido_venda=pedido,
                cliente_nome=pedido.cliente.razao_social,
                peso_kg=Decimal('10'), volumes=Decimal('1'),
            )
        if despachar:
            CarregamentoService.despachar(romaneio, temperatura, self.usuario)
        return romaneio


class ListaTests(EntregaBase):
    """O que aparece na tela."""

    def test_a_parada_da_carga_despachada_aparece(self):
        romaneio = self._carga()

        linhas = EntregaService.paradas(self.filial)

        self.assertEqual(len(linhas), 1)
        self.assertTrue(linhas[0]['pendente'])
        self.assertEqual(linhas[0]['romaneio'], romaneio)

    def test_carga_ainda_na_doca_nao_aparece(self):
        """Enquanto o caminhão não sai, o trabalho é do carregamento."""
        self._carga(despachar=False)

        self.assertEqual(EntregaService.paradas(self.filial), [])

    def test_a_temperatura_da_saida_vem_junto(self):
        """"Saiu a -19 e chegou a -12" é a frase que explica o problema."""
        self._carga(temperatura=Decimal('-19'))

        self.assertEqual(
            EntregaService.paradas(self.filial)[0]['saida'], Decimal('-19.00'),
        )

    def test_pendente_vem_antes_de_resolvida(self):
        romaneio = self._carga(paradas=2)
        primeira, segunda = list(romaneio.itens.all())
        EntregaService.entregar(
            primeira, {'recebido_por': 'Maria'}, self.usuario,
        )

        linhas = EntregaService.paradas(self.filial)

        self.assertEqual(
            [l['parada'] for l in linhas], [segunda, primeira],
        )


class EntregarTests(EntregaBase):
    """O canhoto."""

    def test_a_entrega_guarda_quem_recebeu_quando_e_a_temperatura(self):
        romaneio = self._carga()
        parada = romaneio.itens.first()

        ficha = EntregaService.entregar(parada, {
            'recebido_por': 'Maria Souza', 'documento': '123456',
            'temperatura': Decimal('-17'),
        }, self.usuario)

        parada.refresh_from_db()
        self.assertEqual(parada.status_entrega, SE.ENTREGUE)
        self.assertEqual(ficha.recebido_por, 'Maria Souza')
        self.assertEqual(ficha.documento, '123456')
        self.assertEqual(ficha.temperatura, Decimal('-17.00'))
        self.assertIsNotNone(ficha.entregue_em)
        self.assertEqual(ficha.registrada_por, self.usuario)

    def test_entrega_sem_nome_e_recusada(self):
        """Um "entregue" sem nome não prova nada."""
        romaneio = self._carga()
        parada = romaneio.itens.first()

        with self.assertRaises(DomainError):
            EntregaService.entregar(parada, {'recebido_por': '  '}, self.usuario)

        parada.refresh_from_db()
        self.assertEqual(parada.status_entrega, SE.PENDENTE)

    def test_entrega_sem_temperatura_acontece_e_fica_marcada(self):
        """
        Recusar por falta de termômetro deixaria a entrega sem NENHUM
        canhoto — pior do que um canhoto sem temperatura.
        """
        romaneio = self._carga()
        parada = romaneio.itens.first()

        EntregaService.entregar(parada, {'recebido_por': 'Maria'}, self.usuario)

        linha = EntregaService.paradas(self.filial)[0]
        self.assertTrue(linha['entregue'])
        self.assertTrue(linha['sem_medicao'])

    def test_chegar_acima_do_exigido_e_marcado(self):
        romaneio = self._carga()
        parada = romaneio.itens.first()

        EntregaService.entregar(parada, {
            'recebido_por': 'Maria', 'temperatura': Decimal('-8'),
        }, self.usuario)

        linha = EntregaService.paradas(self.filial)[0]
        self.assertTrue(linha['fora_da_faixa'])
        self.assertEqual(EntregaService.resumo([linha])['fora_da_faixa'], 1)

    def test_dentro_do_exigido_nao_e_alarme(self):
        romaneio = self._carga()
        parada = romaneio.itens.first()

        EntregaService.entregar(parada, {
            'recebido_por': 'Maria', 'temperatura': Decimal('-20'),
        }, self.usuario)

        self.assertFalse(EntregaService.paradas(self.filial)[0]['fora_da_faixa'])


class OcorrenciaTests(EntregaBase):
    """Quando a carga volta."""

    def test_a_nao_entrega_exige_motivo(self):
        romaneio = self._carga()
        parada = romaneio.itens.first()

        with self.assertRaises(DomainError):
            EntregaService.nao_entregar(parada, {'ocorrencia': ''}, self.usuario)

        parada.refresh_from_db()
        self.assertEqual(parada.status_entrega, SE.PENDENTE)

    def test_a_ocorrencia_fica_registrada(self):
        romaneio = self._carga()
        parada = romaneio.itens.first()

        ficha = EntregaService.nao_entregar(parada, {
            'ocorrencia': OC.AUSENTE, 'observacao': 'Loja fechada às 14h.',
        }, self.usuario)

        parada.refresh_from_db()
        self.assertEqual(parada.status_entrega, SE.NAO_ENTREGUE)
        self.assertEqual(ficha.ocorrencia, OC.AUSENTE)
        self.assertIn('14h', ficha.observacao)
        self.assertIsNone(ficha.entregue_em)


class FechamentoTests(EntregaBase):
    """O romaneio fecha sozinho."""

    def test_com_todas_as_paradas_resolvidas_o_romaneio_vira_entregue(self):
        """
        Derivado, e não marcado à mão: um botão "encerrar" seria um segundo
        lugar para dizer a mesma coisa, e os dois discordariam.
        """
        romaneio = self._carga(paradas=2)
        primeira, segunda = list(romaneio.itens.all())

        EntregaService.entregar(primeira, {'recebido_por': 'Maria'}, self.usuario)
        romaneio.refresh_from_db()
        self.assertEqual(romaneio.status, RomaneioCarga.Status.EM_ROTA)

        EntregaService.nao_entregar(
            segunda, {'ocorrencia': OC.RECUSA}, self.usuario,
        )
        romaneio.refresh_from_db()
        self.assertEqual(romaneio.status, RomaneioCarga.Status.ENTREGUE)


class TelaTests(EntregaBase):
    """A tela."""

    def test_a_tela_abre(self):
        self._carga()

        resposta = self.client.get(reverse('polpa:entregas'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Mercado Central')

    def test_a_tela_nao_e_o_placeholder_em_construcao(self):
        resposta = self.client.get(
            reverse('polpa:item', args=['expedicao', 'entregas']),
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, 'Tela em construção')

    def test_registrar_entrega_pela_tela_com_virgula(self):
        romaneio = self._carga()
        parada = romaneio.itens.first()

        self.client.post(reverse('polpa:entregas'), {
            'acao': 'entregar', 'parada': parada.pk,
            'recebido_por': 'Maria Souza', 'temperatura': '-17,5',
        })

        parada.refresh_from_db()
        self.assertEqual(parada.status_entrega, SE.ENTREGUE)
        self.assertEqual(parada.ficha_polpa.temperatura, Decimal('-17.50'))

    def test_registrar_ocorrencia_pela_tela(self):
        romaneio = self._carga()
        parada = romaneio.itens.first()

        self.client.post(reverse('polpa:entregas'), {
            'acao': 'nao-entregar', 'parada': parada.pk,
            'ocorrencia': OC.ENDERECO, 'observacao': 'Rua não existe.',
        })

        parada.refresh_from_db()
        self.assertEqual(parada.status_entrega, SE.NAO_ENTREGUE)

    def test_a_tela_explica_a_recusa_em_vez_de_estourar(self):
        romaneio = self._carga()
        parada = romaneio.itens.first()

        resposta = self.client.post(reverse('polpa:entregas'), {
            'acao': 'entregar', 'parada': parada.pk, 'recebido_por': '',
        }, follow=True)

        self.assertEqual(resposta.status_code, 200)
        parada.refresh_from_db()
        self.assertEqual(parada.status_entrega, SE.PENDENTE)
        self.assertContains(resposta, 'Informe quem recebeu')
