"""
Rendimento real: o que a fruta rendeu contra o que deveria render.

O QUE ESTES TESTES CERCAM:

  · PESO CONTRA PESO, e não produzido contra planejado. "Cumpri a meta?" é
    outra pergunta e já tem tela. Rendimento de polpa é quanto da FRUTA
    virou PRODUTO — 1.000 kg de manga que viram 600 kg de polpa rendem 60%,
    tenha a ordem pedido 500 ou 5.000;

  · O DESVIO VEM EM QUILO. "Menos 2,3 pontos" é abstrato; "1.150 kg de
    fruta que não viraram polpa" é a conversa que a fábrica tem;

  · SEM RÉGUA NÃO É "NO ALVO". Receita ou fruta sem rendimento cadastrado
    aparece como cadastro faltando — nunca como resultado bom;

  · A ATRIBUIÇÃO POR FRUTA É HONESTA OU NÃO EXISTE. Receita com duas frutas
    fica de fora do quadro por fruta: os dois pesos entraram no mesmo
    tanque, e repartir por chute daria um número com cara de medição;

  · SEM ORDEM ENCERRADA O RESULTADO É `None`. Zero se leria como "a fábrica
    não rendeu nada".
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.polpa.models import EtapaReceita, FichaProduto, Fruta, OrdemPolpa
from apps.polpa.services import CatalogoService, ReceitaService
from apps.polpa.services.rendimento import RendimentoService
from apps.producao.models import ItemFichaTecnica, OrdemProducao
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo


class RendimentoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Rendimento LTDA', nome_fantasia='Rendimento',
            cnpj='83145678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='83145678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='KG', descricao='Quilograma',
            tipo=UnidadeMedida.Tipo.PESO,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@rendimento.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    # ── Fixtures ─────────────────────────────────────────────────────────

    def _fruta(self, nome='Manga', rendimento=Decimal('60')):
        return Fruta.objects.create(
            filial=self.filial, nome=nome, rendimento_esperado=rendimento,
        )

    def _mp(self, codigo='MANGA', fruta=None):
        ficha = CatalogoService.salvar(self.filial, {
            'tipo': T.FRUTA, 'descricao': f'Fruta {codigo}', 'codigo': codigo,
            'unidade_medida': self.unidade,
        })
        if fruta is not None:
            ficha.fruta = fruta
            ficha.save(update_fields=['fruta'])
        return ficha.produto

    def _acabado(self, codigo='PM1'):
        return CatalogoService.salvar(self.filial, {
            'tipo': T.POLPA, 'descricao': f'Polpa {codigo}', 'codigo': codigo,
            'unidade_medida': self.unidade, 'validade_dias': 180,
        }).produto

    def _receita(self, acabado, materias, esperado=Decimal('60'), versao='1.0'):
        receita = ReceitaService.criar(self.filial, acabado, {
            'descricao': f'Receita {acabado.codigo}', 'versao': versao,
            'quantidade_produzida': Decimal('1000'),
            'rendimento_esperado': esperado,
        })
        for mp in materias:
            ItemFichaTecnica.objects.create(
                ficha=receita.ficha, materia_prima=mp,
                quantidade=Decimal('100'), perda_prevista=Decimal('0'),
            )
        EtapaReceita.objects.create(receita=receita, ordem=1, nome='Despolpa')
        ReceitaService.ativar(receita)
        return receita

    def _ordem(self, receita, entrada, saida, dias_atras=1):
        fim = timezone.now() - timedelta(days=dias_atras)
        ordem = OrdemProducao.objects.create(
            filial=self.filial,
            numero=f'OP{OrdemProducao.objects.count() + 1:04d}',
            ficha_tecnica=receita.ficha,
            produto_acabado=receita.ficha.produto_acabado,
            quantidade_planejada=Decimal('1000'),
            quantidade_produzida=Decimal('1000'),
            status=OrdemProducao.Status.ENCERRADA,
            peso_entrada_mp=entrada, peso_saida_produzido=saida,
            data_fim_real=fim, usuario_abertura=self.usuario,
        )
        return OrdemPolpa.objects.create(
            filial=self.filial, ordem=ordem, receita=receita,
            situacao=OrdemPolpa.Situacao.PRODUZIDA,
        )


class ResumoTests(RendimentoBase):
    """A fábrica inteira."""

    def test_sem_ordem_encerrada_o_real_e_nulo(self):
        """Zero se leria como "a fábrica não rendeu nada"."""
        resumo = RendimentoService.resumo(self.filial)

        self.assertIsNone(resumo['real'])
        self.assertEqual(resumo['ordens'], 0)

    def test_o_real_e_peso_que_saiu_sobre_peso_que_entrou(self):
        receita = self._receita(self._acabado(), [self._mp()])
        self._ordem(receita, Decimal('1000'), Decimal('600'))

        resumo = RendimentoService.resumo(self.filial)

        self.assertEqual(resumo['real'], Decimal('60.00'))
        self.assertEqual(resumo['esperado'], Decimal('60.00'))
        self.assertEqual(resumo['desvio'], Decimal('0.00'))

    def test_abaixo_do_esperado_vira_quilo_de_fruta(self):
        """
        "Menos 2 pontos" é abstrato; 20 kg de fruta comprada, descascada e
        não vendida é a conversa real.
        """
        receita = self._receita(self._acabado(), [self._mp()])
        self._ordem(receita, Decimal('1000'), Decimal('580'))

        resumo = RendimentoService.resumo(self.filial)

        self.assertEqual(resumo['real'], Decimal('58.00'))
        self.assertEqual(resumo['desvio'], Decimal('-2.00'))
        self.assertEqual(resumo['kg_perdidos'], Decimal('20.000'))
        self.assertTrue(resumo['abaixo'])

    def test_acima_do_esperado_nao_gera_perda(self):
        receita = self._receita(self._acabado(), [self._mp()])
        self._ordem(receita, Decimal('1000'), Decimal('650'))

        resumo = RendimentoService.resumo(self.filial)

        self.assertEqual(resumo['desvio'], Decimal('5.00'))
        self.assertIsNone(resumo['kg_perdidos'])
        self.assertFalse(resumo['abaixo'])

    def test_o_esperado_da_fabrica_e_ponderado_pelo_peso(self):
        """
        Uma receita rodada uma vez não pode pesar o mesmo que a que roda
        todo dia — a média simples faria o alvo da fábrica flutuar sozinho.
        """
        muita = self._receita(
            self._acabado('PM1'), [self._mp('MG')], esperado=Decimal('60'),
        )
        pouca = self._receita(
            self._acabado('PM2'), [self._mp('AC')], esperado=Decimal('30'),
        )
        self._ordem(muita, Decimal('9000'), Decimal('5400'))
        self._ordem(pouca, Decimal('1000'), Decimal('300'))

        resumo = RendimentoService.resumo(self.filial)

        # (9000×60% + 1000×30%) / 10000 = 57%
        self.assertEqual(resumo['esperado'], Decimal('57.00'))

    def test_ordem_sem_peso_de_entrada_fica_de_fora(self):
        """
        Ordem que ninguém pesou entra com zero na entrada — e dividir por
        ela inventaria uma perda que ninguém teve.
        """
        receita = self._receita(self._acabado(), [self._mp()])
        self._ordem(receita, Decimal('1000'), Decimal('600'))
        self._ordem(receita, Decimal('0'), Decimal('600'))

        self.assertEqual(RendimentoService.resumo(self.filial)['ordens'], 1)

    def test_fora_da_janela_nao_entra(self):
        receita = self._receita(self._acabado(), [self._mp()])
        self._ordem(receita, Decimal('1000'), Decimal('600'), dias_atras=200)

        self.assertEqual(RendimentoService.resumo(self.filial)['ordens'], 0)

    def test_receita_sem_rendimento_esperado_e_contada_como_sem_regua(self):
        """Sem régua não é "no alvo": é cadastro faltando."""
        # A receita só ATIVA com rendimento declarado (é regra da seção 2);
        # sem régua é o caso da receita antiga, de antes dessa exigência.
        receita = self._receita(self._acabado(), [self._mp()])
        receita.rendimento_esperado = None
        receita.save(update_fields=['rendimento_esperado'])
        self._ordem(receita, Decimal('1000'), Decimal('600'))

        resumo = RendimentoService.resumo(self.filial)

        self.assertEqual(resumo['sem_regua'], 1)
        self.assertIsNone(resumo['esperado'])
        self.assertIsNone(resumo['desvio'])


class PorFrutaTests(RendimentoBase):
    """A régua do cadastro de cada fruta."""

    def test_a_fruta_e_comparada_com_a_regua_dela(self):
        fruta = self._fruta(rendimento=Decimal('60'))
        receita = self._receita(
            self._acabado(), [self._mp('MG', fruta)], esperado=Decimal('55'),
        )
        self._ordem(receita, Decimal('1000'), Decimal('570'))

        dados = RendimentoService.por_fruta(self.filial)

        linha = dados['linhas'][0]
        self.assertEqual(linha['fruta'], fruta)
        self.assertEqual(linha['real'], Decimal('57.00'))
        # Contra a régua da FRUTA (60), não a da receita (55).
        self.assertEqual(linha['esperado'], Decimal('60.00'))
        self.assertEqual(linha['desvio'], Decimal('-3.00'))

    def test_receita_com_duas_frutas_fica_de_fora_e_e_contada(self):
        """
        Os dois pesos entraram no mesmo tanque: repartir por chute daria um
        número com cara de medição.
        """
        manga = self._fruta('Manga')
        acerola = self._fruta('Acerola', Decimal('40'))
        receita = self._receita(
            self._acabado(), [self._mp('MG', manga), self._mp('AC', acerola)],
        )
        self._ordem(receita, Decimal('1000'), Decimal('600'))

        dados = RendimentoService.por_fruta(self.filial)

        self.assertEqual(dados['linhas'], [])
        self.assertEqual(dados['misturadas'], 1)

    def test_materia_prima_sem_fruta_vinculada_e_contada_a_parte(self):
        receita = self._receita(self._acabado(), [self._mp('MG')])
        self._ordem(receita, Decimal('1000'), Decimal('600'))

        dados = RendimentoService.por_fruta(self.filial)

        self.assertEqual(dados['linhas'], [])
        self.assertEqual(dados['sem_fruta'], 1)

    def test_fruta_sem_rendimento_cadastrado_aparece_sem_regua(self):
        fruta = self._fruta(rendimento=None)
        receita = self._receita(self._acabado(), [self._mp('MG', fruta)])
        self._ordem(receita, Decimal('1000'), Decimal('600'))

        linha = RendimentoService.por_fruta(self.filial)['linhas'][0]

        self.assertTrue(linha['sem_esperado'])
        self.assertIsNone(linha['desvio'])


class PorProdutoTests(RendimentoBase):
    """Cada produto contra a receita dele."""

    def test_o_pior_vem_primeiro(self):
        """Quem abre a tela quer saber onde está perdendo."""
        bom = self._receita(self._acabado('PM1'), [self._mp('MG')])
        ruim = self._receita(self._acabado('PM2'), [self._mp('AC')])
        self._ordem(bom, Decimal('1000'), Decimal('600'))
        self._ordem(ruim, Decimal('1000'), Decimal('500'))

        linhas = RendimentoService.por_produto(self.filial)

        self.assertEqual(linhas[0]['produto'], ruim.ficha.produto_acabado)
        self.assertEqual(linhas[0]['desvio'], Decimal('-10.00'))

    def test_as_ordens_do_mesmo_produto_somam(self):
        receita = self._receita(self._acabado(), [self._mp()])
        self._ordem(receita, Decimal('1000'), Decimal('600'))
        self._ordem(receita, Decimal('1000'), Decimal('500'))

        linha = RendimentoService.por_produto(self.filial)[0]

        self.assertEqual(linha['ordens'], 2)
        self.assertEqual(linha['real'], Decimal('55.00'))


class PorOrdemTests(RendimentoBase):
    """A média esconde o dia ruim."""

    def test_cada_batida_com_o_seu_rendimento(self):
        receita = self._receita(self._acabado(), [self._mp()])
        self._ordem(receita, Decimal('1000'), Decimal('600'), dias_atras=2)
        self._ordem(receita, Decimal('1000'), Decimal('500'), dias_atras=1)

        linhas = RendimentoService.por_ordem(self.filial)

        # Mais recente primeiro.
        self.assertEqual(linhas[0]['real'], Decimal('50.00'))
        self.assertEqual(linhas[1]['real'], Decimal('60.00'))


class TelaTests(RendimentoBase):
    """A tela."""

    def test_a_tela_abre(self):
        receita = self._receita(self._acabado(), [self._mp()])
        op = self._ordem(receita, Decimal('1000'), Decimal('580'))

        resposta = self.client.get(reverse('polpa:rendimento-real'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, op.numero)
        self.assertContains(resposta, '58,00%')

    def test_a_tela_nao_e_o_placeholder_em_construcao(self):
        resposta = self.client.get(
            reverse('polpa:item', args=['indicadores', 'rendimento-real']),
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, 'Tela em construção')

    def test_a_janela_muda_pela_url(self):
        receita = self._receita(self._acabado(), [self._mp()])
        self._ordem(receita, Decimal('1000'), Decimal('600'), dias_atras=45)

        curta = self.client.get(reverse('polpa:rendimento-real'), {'dias': 7})
        longa = self.client.get(reverse('polpa:rendimento-real'), {'dias': 90})

        self.assertEqual(curta.context['resumo']['ordens'], 0)
        self.assertEqual(longa.context['resumo']['ordens'], 1)

    def test_janela_invalida_cai_no_padrao(self):
        resposta = self.client.get(reverse('polpa:rendimento-real'), {'dias': 'x'})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['dias'], RendimentoService.JANELA)
