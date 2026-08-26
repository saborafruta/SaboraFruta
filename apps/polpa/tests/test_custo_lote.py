"""
Custo por lote: qual lote está custando caro.

O QUE ESTES TESTES CERCAM:

  · A CONTA NÃO É REFEITA AQUI. Quem soma custo é o `CustoService`, o mesmo
    que a tela da ordem usa. Este recorte só percorre os lotes — um segundo
    lugar somando daria dois custos para o mesmo lote, e a divergência
    apareceria na hora de formar preço;

  · SÓ ORDEM CONCLUÍDA. Ordem em andamento já consumiu fruta e ainda não
    gerou produto: entraria como o lote mais caro da fábrica justamente por
    estar no meio do caminho;

  · A FATIA EM PERCENTUAL É O QUE COMPARA. R$ 3.000 de fruta não dizem nada
    sozinhos — dependem do tamanho da batida. "78% do custo é fruta" diz;

  · A MÉDIA POR QUILO É PONDERADA PELO PESO. Média de médias deixa um lote
    de 20 kg puxar o custo da fábrica tanto quanto um de 2.000;

  · ONDE A CONTA NÃO EXISTE, É `None`. Zero por quilo se leria como "de
    graça", e é assim que um produto entra na tabela de preço abaixo do
    custo.
"""
from decimal import Decimal

from django.urls import reverse

from apps.polpa.models import OrdemPolpa
from apps.polpa.services import OrdemPolpaService
from apps.polpa.services.custo import CustoService
from apps.polpa.tests.test_custo import CustoBase

S = OrdemPolpa.Situacao
ZERO = Decimal('0')


class CustoPorLoteBase(CustoBase):

    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def _mais_uma(self, quantidade='1000'):
        """
        Outra ordem sobre o estoque que a primeira já criou.

        `_op` cadastra o lote de matéria-prima com número fixo; chamá-lo
        duas vezes esbarraria na unicidade do lote, que é regra do ERP e não
        deste teste.
        """
        return OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal(quantidade)}, self.usuario,
        )


class ListaTests(CustoPorLoteBase):
    """O que entra na lista."""

    def test_o_lote_produzido_aparece_com_o_custo(self):
        op = self._op()
        self._produzir(op)

        linhas = CustoService.por_lote(self.filial)

        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]['op'], op)
        self.assertIsNotNone(linhas[0]['total'])

    def test_ordem_em_andamento_nao_entra(self):
        """
        Ela já consumiu fruta e ainda não gerou produto — entraria como o
        lote mais caro da fábrica por estar no meio do caminho.
        """
        op = self._op()
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)

        self.assertEqual(CustoService.por_lote(self.filial), [])

    def test_o_mais_recente_vem_primeiro(self):
        primeira = self._op()
        self._produzir(primeira)
        segunda = self._mais_uma()
        self._produzir(segunda)

        linhas = CustoService.por_lote(self.filial)

        self.assertEqual([l['op'] for l in linhas], [segunda, primeira])

    def test_fora_da_janela_nao_entra(self):
        op = self._op()
        self._produzir(op)

        self.assertEqual(len(CustoService.por_lote(self.filial, dias=0)), 0)


class ComposicaoTests(CustoPorLoteBase):
    """Fruta, embalagem e processo."""

    def test_as_tres_fatias_somam_cem(self):
        op = self._op()
        self._produzir(op)

        fatias = CustoService.por_lote(self.filial)[0]['fatias']

        soma = (
            fatias['materia_prima'] + fatias['embalagem'] + fatias['processo']
        )
        self.assertEqual(soma.quantize(Decimal('1')), Decimal('100'))

    def test_o_processo_junta_o_que_nao_e_fruta_nem_embalagem(self):
        """
        Seis colunas que ninguém compara viram uma que responde "quanto
        custa transformar".
        """
        op = self._op()
        self._produzir(op)

        linha = CustoService.por_lote(self.filial)[0]
        realizado = CustoService.realizado(op)

        esperado = (
            realizado['mao_de_obra'] + realizado['indireto']
            + realizado['extras'] + realizado['perdas']
        )
        self.assertEqual(linha['processo'], esperado)

    def test_a_conta_e_a_mesma_da_tela_da_ordem(self):
        """Um segundo lugar somando daria dois custos para o mesmo lote."""
        op = self._op()
        self._produzir(op)

        linha = CustoService.por_lote(self.filial)[0]

        self.assertEqual(linha['total'], CustoService.realizado(op)['total'])
        self.assertEqual(
            linha['desvio'], CustoService.comparar(op)['desvio_total'],
        )


class ResumoTests(CustoPorLoteBase):
    """O topo da tela."""

    def test_o_total_soma_os_lotes_da_janela(self):
        primeira = self._op()
        self._produzir(primeira)
        segunda = self._mais_uma()
        self._produzir(segunda)

        linhas = CustoService.por_lote(self.filial)
        resumo = CustoService.resumo_lotes(linhas)

        self.assertEqual(resumo['lotes'], 2)
        self.assertEqual(
            resumo['total'],
            (linhas[0]['total'] + linhas[1]['total']),
        )

    def test_a_media_por_quilo_e_ponderada_pelo_peso(self):
        """
        Média de médias deixaria um lote pequeno e caro virar alarme da
        fábrica inteira.
        """
        grande = self._op()
        self._produzir(grande)
        pequena = self._mais_uma(quantidade='50')
        self._produzir(pequena)

        linhas = CustoService.por_lote(self.filial)
        resumo = CustoService.resumo_lotes(linhas)

        peso = sum(l['total'] / l['por_kg'] for l in linhas)
        self.assertEqual(
            resumo['por_kg'], (resumo['total'] / peso).quantize(Decimal('0.0001')),
        )

    def test_sem_lote_nenhum_o_custo_por_quilo_e_nulo(self):
        """Zero se leria como "custa nada por quilo"."""
        resumo = CustoService.resumo_lotes([])

        self.assertIsNone(resumo['por_kg'])
        self.assertEqual(resumo['lotes'], 0)

    def test_o_desvio_conta_so_o_que_passou_do_previsto(self):
        op = self._op()
        self._produzir(op)

        linhas = CustoService.por_lote(self.filial)
        resumo = CustoService.resumo_lotes(linhas)

        acima = [l for l in linhas if l['acima']]
        self.assertEqual(resumo['acima_do_previsto'], len(acima))


class TelaTests(CustoPorLoteBase):
    """A tela."""

    def test_a_tela_abre(self):
        op = self._op()
        self._produzir(op)

        resposta = self.client.get(reverse('polpa:custos'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, op.lote.numero_lote)

    def test_a_tela_nao_e_o_placeholder_em_construcao(self):
        resposta = self.client.get(
            reverse('polpa:item', args=['indicadores', 'custos']),
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, 'Tela em construção')

    def test_a_janela_muda_pela_url(self):
        op = self._op()
        self._produzir(op)

        resposta = self.client.get(reverse('polpa:custos'), {'dias': 7})

        self.assertEqual(resposta.context['dias'], 7)
        self.assertEqual(resposta.context['resumo']['lotes'], 1)

    def test_janela_invalida_cai_no_padrao(self):
        resposta = self.client.get(reverse('polpa:custos'), {'dias': 'x'})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['dias'], 90)
