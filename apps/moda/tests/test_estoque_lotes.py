"""
Lotes — de que rolo saiu cada peça.

ESTA TELA EXISTE PARA UM DIA RUIM. O cliente liga dizendo que a camisa
encolheu, e a pergunta seguinte é sempre a mesma: que outros pedidos levaram
peça daquele rolo? Se a resposta vier errada, ou se troca o que não precisa
ou se deixa peça defeituosa na rua — os dois caros.

O que os testes cercam:

  · a CORRENTE lote → corte → ordem → pedido → cliente, lida de trás para a
    frente. É ela que responde a pergunta do dia ruim;
  · a NORMALIZAÇÃO do lote, que é texto digitado: juntar de menos espalha a
    mesma peça em vários rolos, juntar demais mistura rolos diferentes e
    manda trocar peça boa;
  · o refugo contado UMA vez por ordem, mesmo quando o lote tem vários
    cortes dela — o refugo é da ordem, não do enfesto;
  · corte sem lote não pode sumir da tela: é o furo da rastreabilidade, e
    ele não se conserta depois do fato.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.moda.models import (
    EtapaOrdem, ItemPedidoProducao, OrdemProducao, PedidoProducao, ProdutoModa,
    RegistroCorte, Tecido,
)
from apps.moda.services.estoque_lote import (
    REFUGO_SUSPEITO, EstoqueLoteService, chave_do_lote,
)


class LoteBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Lote LTDA', nome_fantasia='Lote',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Lote LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )

    def setUp(self):
        self.produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        self.tecido = Tecido.objects.create(filial=self.filial, nome='Malha Dry')
        self._n = 0

    # ── Montagem ─────────────────────────────────────────────────────────

    def _cliente(self, nome='Time do Bairro', cpf='12345678901'):
        return Cliente.objects.create(
            filial=self.filial, razao_social=nome, cpf_cnpj=cpf,
        )

    def _ordem(self, cliente=None, quantidade=100):
        self._n += 1
        pedido = PedidoProducao.objects.create(
            filial=self.filial, numero=self._n,
            cliente=cliente or self._cliente(
                f'Cliente {self._n}', f'1234567890{self._n}',
            ),
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=self.produto, descricao='Camisa',
            quantidade=quantidade, tecido=self.tecido,
        )
        return OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            numero=f'OP-{self._n:04d}', ano=2026, sequencial=self._n,
            quantidade=quantidade,
        )

    def _corte(self, ordem, lote='L-123', consumo='100', quantidade=100,
               aproveitamento='0', dias_atras=1, tecido=None,
               status=RegistroCorte.Status.CORTADO):
        self._n += 1
        return RegistroCorte.objects.create(
            filial=self.filial, ordem=ordem, numero=self._n, status=status,
            lote=lote, tecido=tecido if tecido is not None else self.tecido,
            quantidade=quantidade,
            data=timezone.localdate() - timedelta(days=dias_atras),
            consumo_real=Decimal(consumo),
            aproveitamento=Decimal(aproveitamento),
        )

    def _perda(self, ordem, perda, produzido, etapa=EtapaOrdem.Etapa.COSTURA,
               sequencia=6):
        return EtapaOrdem.objects.create(
            ordem=ordem, etapa=etapa, sequencia=sequencia,
            status=EtapaOrdem.Status.CONCLUIDA,
            quantidade_produzida=produzido, perda=perda,
            data_conclusao=timezone.localdate(),
        )

    def _painel(self, busca=''):
        return EstoqueLoteService.painel(self.filial, busca)

    def _lotes(self):
        return {l['lote']: l for l in self._painel()['linhas']}


class ChaveTests(TestCase):
    """O lote é texto digitado, e é essa a fragilidade da corrente."""

    def test_espaco_e_caixa_somem(self):
        """
        "L-123", "l-123 " e " L-123" são o mesmo rolo para quem está na mesa
        de corte. Separá-los espalharia a mesma peça em três lotes.
        """
        self.assertEqual(chave_do_lote('L-123'), 'L-123')
        self.assertEqual(chave_do_lote('l-123 '), 'L-123')
        self.assertEqual(chave_do_lote('  L-123  '), 'L-123')

    def test_espaco_no_meio_e_normalizado(self):
        self.assertEqual(chave_do_lote('L  123'), 'L 123')

    def test_o_resto_nao_e_adivinhado(self):
        """
        Juntar "L-123" com "L123" por semelhança esconderia justamente o
        cadastro torto — e, no dia do defeito, mandaria trocar peça boa.
        """
        self.assertNotEqual(chave_do_lote('L-123'), chave_do_lote('L123'))

    def test_vazio_continua_vazio(self):
        self.assertEqual(chave_do_lote(''), '')
        self.assertEqual(chave_do_lote('   '), '')
        self.assertEqual(chave_do_lote(None), '')


class CorrenteTests(LoteBase):
    """lote → corte → ordem → pedido → cliente."""

    def test_o_lote_leva_aos_clientes_que_receberam(self):
        """A pergunta do dia ruim: quem mais levou peça deste rolo?"""
        a = self._ordem(cliente=self._cliente('Escola Sao Jose', '11111111111'))
        b = self._ordem(cliente=self._cliente('Time do Bairro', '22222222222'))
        self._corte(a, lote='L-123')
        self._corte(b, lote='L-123')

        linha = self._lotes()['L-123']

        self.assertEqual(linha['qtd_ordens'], 2)
        self.assertEqual(linha['qtd_pedidos'], 2)
        self.assertEqual(linha['qtd_clientes'], 2)
        self.assertEqual(linha['clientes'], ['Escola Sao Jose', 'Time do Bairro'])

    def test_junta_os_cortes_do_mesmo_rolo_apesar_da_digitacao(self):
        a = self._ordem()
        b = self._ordem()
        self._corte(a, lote='L-123')
        self._corte(b, lote=' l-123 ')

        linhas = self._painel()['linhas']

        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]['cortes'], 2)

    def test_o_mesmo_numero_em_tecidos_diferentes_sao_rolos_diferentes(self):
        """
        Número de lote é do fornecedor. O mesmo "L-123" num Piquet e numa
        Malha Dry são dois rolos, e misturá-los mandaria trocar peça boa.
        """
        piquet = Tecido.objects.create(filial=self.filial, nome='Piquet')
        a = self._ordem()
        b = self._ordem()
        self._corte(a, lote='L-123')
        self._corte(b, lote='L-123', tecido=piquet)

        self.assertEqual(len(self._painel()['linhas']), 2)

    def test_soma_metros_e_pecas_do_rolo(self):
        a = self._ordem()
        b = self._ordem()
        self._corte(a, lote='L-123', consumo='120', quantidade=100)
        self._corte(b, lote='L-123', consumo='80', quantidade=60)

        linha = self._lotes()['L-123']

        self.assertEqual(linha['metros'], Decimal('200.00'))
        self.assertEqual(linha['pecas'], 160)

    def test_guarda_o_primeiro_e_o_ultimo_corte(self):
        a = self._ordem()
        b = self._ordem()
        self._corte(a, lote='L-123', dias_atras=30)
        self._corte(b, lote='L-123', dias_atras=5)

        linha = self._lotes()['L-123']
        hoje = timezone.localdate()

        self.assertEqual(linha['primeiro'], hoje - timedelta(days=30))
        self.assertEqual(linha['ultimo'], hoje - timedelta(days=5))

    def test_corte_apenas_planejado_nao_conta(self):
        """O planejado ainda não tirou pano do rolo."""
        ordem = self._ordem()
        self._corte(ordem, lote='L-123', status=RegistroCorte.Status.PLANEJADO)

        self.assertEqual(self._painel()['linhas'], [])


class RefugoTests(LoteBase):
    """Um rolo ruim se anuncia antes da reclamação."""

    def test_o_refugo_do_lote_vem_das_ordens_que_o_usaram(self):
        ordem = self._ordem(quantidade=100)
        self._corte(ordem, lote='L-123')
        self._perda(ordem, perda=10, produzido=90)

        linha = self._lotes()['L-123']

        self.assertEqual(linha['perda'], 10)
        self.assertEqual(linha['percentual_perda'], Decimal('10.0'))
        self.assertTrue(linha['suspeito'])

    def test_o_refugo_conta_uma_vez_por_ordem(self):
        """
        Dois enfestos da MESMA ordem no mesmo rolo não dobram o refugo: ele
        é da ordem, não do enfesto.
        """
        ordem = self._ordem(quantidade=100)
        self._corte(ordem, lote='L-123', consumo='50', quantidade=50)
        self._corte(ordem, lote='L-123', consumo='50', quantidade=50)
        self._perda(ordem, perda=10, produzido=90)

        linha = self._lotes()['L-123']

        self.assertEqual(linha['cortes'], 2)
        self.assertEqual(linha['perda'], 10)

    def test_refugo_baixo_nao_e_suspeito(self):
        ordem = self._ordem(quantidade=100)
        self._corte(ordem, lote='L-123')
        self._perda(ordem, perda=1, produzido=99)

        linha = self._lotes()['L-123']

        self.assertEqual(linha['percentual_perda'], Decimal('1.0'))
        self.assertFalse(linha['suspeito'])
        self.assertLess(linha['percentual_perda'], REFUGO_SUSPEITO)

    def test_sem_etapa_concluida_o_refugo_e_traco_e_nao_zero(self):
        """
        Zero diria "este rolo é ótimo" antes de qualquer peça ter passado
        pela costura — e um rolo sem histórico não é um rolo bom.
        """
        ordem = self._ordem()
        self._corte(ordem, lote='L-123')

        linha = self._lotes()['L-123']

        self.assertIsNone(linha['percentual_perda'])
        self.assertFalse(linha['suspeito'])

    def test_etapa_administrativa_nao_entra_no_refugo(self):
        ordem = self._ordem(quantidade=100)
        self._corte(ordem, lote='L-123')
        self._perda(ordem, perda=9, produzido=100,
                    etapa=EtapaOrdem.Etapa.PLANEJAMENTO, sequencia=2)

        self.assertIsNone(self._lotes()['L-123']['percentual_perda'])

    def test_o_aproveitamento_e_ponderado_pelo_tecido_gasto(self):
        """Um enfesto de 2 m não pesa igual a um de 200 m."""
        a = self._ordem()
        b = self._ordem()
        self._corte(a, lote='L-123', consumo='200', aproveitamento='90')
        self._corte(b, lote='L-123', consumo='2', aproveitamento='50')

        self.assertEqual(self._lotes()['L-123']['aproveitamento'], Decimal('89.6'))

    def test_os_suspeitos_vem_primeiro_e_o_pior_no_topo(self):
        bom = self._ordem(quantidade=100)
        ruim = self._ordem(quantidade=100)
        pior = self._ordem(quantidade=100)
        self._corte(bom, lote='L-BOM')
        self._corte(ruim, lote='L-RUIM')
        self._corte(pior, lote='L-PIOR')
        self._perda(bom, perda=1, produzido=99)
        self._perda(ruim, perda=8, produzido=92)
        self._perda(pior, perda=20, produzido=80)

        painel = self._painel()

        self.assertEqual(
            [l['lote'] for l in painel['linhas']], ['L-PIOR', 'L-RUIM', 'L-BOM'],
        )
        self.assertEqual(painel['resumo']['suspeitos'], 2)
        self.assertEqual(painel['resumo']['pior']['lote'], 'L-PIOR')


class SemRastroTests(LoteBase):
    """O furo que não se conserta depois do fato."""

    def test_corte_sem_lote_nao_vira_linha_mas_e_contado(self):
        ordem = self._ordem(quantidade=100)
        self._corte(ordem, lote='', consumo='120', quantidade=100)

        painel = self._painel()

        self.assertEqual(painel['linhas'], [])
        self.assertEqual(painel['sem_lote']['cortes'], 1)
        self.assertEqual(painel['sem_lote']['pecas'], 100)
        self.assertEqual(painel['resumo']['sem_rastro'], 100)

    def test_lote_so_com_espaco_conta_como_sem_lote(self):
        ordem = self._ordem()
        self._corte(ordem, lote='   ')

        self.assertEqual(self._painel()['sem_lote']['cortes'], 1)

    def test_a_cobertura_mostra_quanto_do_que_saiu_e_rastreavel(self):
        com = self._ordem()
        sem = self._ordem()
        self._corte(com, lote='L-123', quantidade=75)
        self._corte(sem, lote='', quantidade=25)

        self.assertEqual(self._painel()['resumo']['cobertura'], Decimal('75.0'))

    def test_sem_corte_nenhum_a_cobertura_e_none_e_nao_zero(self):
        painel = self._painel()

        self.assertIsNone(painel['resumo']['cobertura'])
        self.assertEqual(painel['linhas'], [])
        self.assertIsNone(painel['resumo']['pior'])


class BuscaTests(LoteBase):
    """Quem chega aqui costuma ter a reclamação, não o número do rolo."""

    def _cenario(self):
        escola = self._ordem(cliente=self._cliente('Escola Sao Jose', '11111111111'))
        time = self._ordem(cliente=self._cliente('Time do Bairro', '22222222222'))
        self._corte(escola, lote='L-123')
        self._corte(time, lote='L-999')
        return escola, time

    def test_acha_pelo_lote(self):
        self._cenario()

        self.assertEqual(
            [l['lote'] for l in self._painel(busca='999')['linhas']], ['L-999'],
        )

    def test_acha_pelo_cliente(self):
        """É do cliente que se anda para trás até o rolo."""
        self._cenario()

        self.assertEqual(
            [l['lote'] for l in self._painel(busca='escola')['linhas']], ['L-123'],
        )

    def test_acha_pelo_numero_da_ordem(self):
        escola, _ = self._cenario()

        self.assertEqual(
            [l['lote'] for l in self._painel(busca=escola.numero)['linhas']],
            ['L-123'],
        )

    def test_acha_pelo_tecido(self):
        self._cenario()

        self.assertEqual(len(self._painel(busca='malha')['linhas']), 2)

    def test_busca_sem_par_devolve_vazio(self):
        self._cenario()

        self.assertEqual(self._painel(busca='xyz')['linhas'], [])

    def test_a_busca_nao_muda_o_resumo(self):
        """O cabeçalho descreve a fábrica, não o recorte da tela."""
        self._cenario()

        painel = self._painel(busca='999')

        self.assertEqual(len(painel['linhas']), 1)
        self.assertEqual(painel['resumo']['lotes'], 2)


class TelaLoteTests(TestCase):
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
        resposta = self.client.get(reverse('moda:estoque-lotes'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Nenhum lote registrado')

    def test_a_tela_abre_mostrando_o_cliente_atingido(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Escola Sao Jose',
            cpf_cnpj='12345678901',
        )
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        tecido = Tecido.objects.create(filial=self.filial, nome='Malha Dry')
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=cliente, numero=1,
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa',
            quantidade=100, tecido=tecido,
        )
        ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            numero='OP-0001', ano=2026, sequencial=1, quantidade=100,
        )
        RegistroCorte.objects.create(
            filial=self.filial, ordem=ordem, numero=1,
            status=RegistroCorte.Status.CORTADO, lote='L-123', tecido=tecido,
            quantidade=100, data=timezone.localdate(),
            consumo_real=Decimal('120'), aproveitamento=Decimal('88'),
        )
        EtapaOrdem.objects.create(
            ordem=ordem, etapa=EtapaOrdem.Etapa.COSTURA, sequencia=6,
            status=EtapaOrdem.Status.CONCLUIDA, quantidade_produzida=90,
            perda=10, data_conclusao=timezone.localdate(),
        )

        resposta = self.client.get(reverse('moda:estoque-lotes'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'L-123')
        self.assertContains(resposta, 'Escola Sao Jose')
        self.assertContains(resposta, '10,0%')

    def test_a_tela_avisa_o_corte_sem_lote(self):
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social='Time', cpf_cnpj='12345678901',
        )
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo='CAM001', nome='Camisa',
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=cliente, numero=1,
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa', quantidade=50,
        )
        ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            numero='OP-0001', ano=2026, sequencial=1, quantidade=50,
        )
        RegistroCorte.objects.create(
            filial=self.filial, ordem=ordem, numero=1,
            status=RegistroCorte.Status.CORTADO, lote='', quantidade=50,
            data=timezone.localdate(), consumo_real=Decimal('60'),
        )

        resposta = self.client.get(reverse('moda:estoque-lotes'))

        self.assertContains(resposta, 'sem lote informado')
        self.assertContains(resposta, 'peça(s) sem rastro')

    def test_a_rota_do_menu_cai_na_tela(self):
        from apps.moda.views_estoque_lote import EstoqueLoteView

        for url in (
            reverse('moda:estoque-lotes'),
            reverse('moda:item', args=['estoque', 'lotes']),
        ):
            self.assertIs(resolve(url).func.view_class, EstoqueLoteView)
