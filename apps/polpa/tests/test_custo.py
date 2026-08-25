"""
Custo industrial: o previsto da receita contra o realizado da batida.

`ReceitaService.custos` já dava o previsto bem: matéria-prima com a perda por
dentro, embalagem separada, processo, e as divisões por unidade, quilo e
caixa. Faltavam três coisas: o REALIZADO na mesma forma, as categorias que a
ficha do ERP não tem campo para guardar (energia à frente), e a comparação.

E havia uma armadilha: `op_service` soma fruta e pote em `custo_materia_prima`.
Comparar esse total com um previsto que separa os dois diria "matéria-prima
subiu" quando quem subiu foi a embalagem.

O que os testes cercam:

  · O REALIZADO SAI DO LOTE QUE O FEFO CONSUMIU, não do custo médio de hoje:
    a manga da semana passada custou o que custou;
  · FRUTA E POTE SEPARADOS no realizado, como já eram no previsto — senão a
    comparação acusa a categoria errada;
  · ENERGIA E OUTROS são cadastráveis, com base que muda a conta: por batida,
    por quilo ou por unidade;
  · PERDA ENTRA NO REALIZADO E NÃO NO PREVISTO. A prevista já está dentro da
    matéria-prima; somá-la de novo contaria a mesma fruta duas vezes;
  · O DESVIO É POR CATEGORIA. "Custo 8% acima" não diz o que fazer;
    "matéria-prima 22% acima" manda olhar a compra de fruta.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque, LoteProduto, MovimentacaoEstoque
from apps.polpa.models import CustoReceita, EtapaReceita, FichaProduto, OrdemPolpa
from apps.polpa.services import CatalogoService, OrdemPolpaService, ReceitaService
from apps.polpa.services.custo import CustoService
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import UnidadeMedida, UnidadeMedidaFilial

T = FichaProduto.Tipo
S = OrdemPolpa.Situacao
B = CustoReceita.Base
ZERO = Decimal('0')


class CustoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Custo LTDA', nome_fantasia='Custo',
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
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@custo.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.acabado = self._item(
            T.POLPA, 'Polpa de manga 100 g', validade_dias=180,
            peso_liquido=Decimal('0.100'),
            quantidade_por_embalagem=Decimal('50'),
        )
        self.manga = self._item(T.FRUTA, 'Manga in natura', custo=Decimal('2'))
        self.pote = self._item(T.POTE, 'Pote 100 g', custo=Decimal('0.50'))
        self.receita = self._receita()

    def _item(self, tipo, descricao, custo=Decimal('0'), **extras):
        dados = {
            'tipo': tipo, 'descricao': descricao, 'codigo': descricao[:10],
            'unidade_medida': self.unidade, 'preco_custo': custo,
            'preco_custo_medio': custo,
        }
        dados.update(extras)
        return CatalogoService.salvar(self.filial, dados).produto

    def _receita(self):
        """1.000 unidades: 100 kg de manga (R$200) + 1.000 potes (R$500)."""
        receita = ReceitaService.criar(self.filial, self.acabado, {
            'descricao': 'Polpa de manga 100 g', 'versao': '1.0',
            'quantidade_produzida': Decimal('1000'),
            'rendimento_esperado': Decimal('60'),
            'custo_mao_obra_padrao': Decimal('300'),
            'custo_indireto_padrao': Decimal('100'),
        })
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.manga,
            quantidade=Decimal('100'), perda_prevista=ZERO,
        )
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.pote,
            quantidade=Decimal('1000'), perda_prevista=ZERO,
        )
        EtapaReceita.objects.create(receita=receita, ordem=1, nome='Despolpa')
        ReceitaService.ativar(receita)
        return receita

    def _estoque(self, produto, quantidade, custo):
        estoque, _ = Estoque.objects.get_or_create(
            produto=produto, filial=self.filial,
        )
        estoque.quantidade_atual = Decimal(quantidade)
        estoque.quantidade_reservada = ZERO
        estoque.atualizar_disponivel()
        estoque.save()
        LoteProduto.objects.create(
            filial=self.filial, produto=produto,
            numero_lote=f'L-{produto.pk}', quantidade_inicial=Decimal(quantidade),
            quantidade_atual=Decimal(quantidade), custo_unitario=Decimal(custo),
            data_validade=timezone.localdate() + timedelta(days=365),
            status=LoteProduto.Status.ATIVO,
        )

    def _op(self, quantidade='1000', custo_manga='2', custo_pote='0.50'):
        self._estoque(self.manga, '10000', custo_manga)
        self._estoque(self.pote, '100000', custo_pote)
        return OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal(quantidade)}, self.usuario,
        )

    def _produzir(self, op, quantidade=None):
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)
        return OrdemPolpaService.concluir(
            op, self.usuario,
            quantidade=Decimal(quantidade or op.quantidade_planejada),
        )


class PrevistoTests(CustoBase):

    def test_o_previsto_separa_fruta_de_embalagem(self):
        op = self._op()

        previsto = CustoService.previsto(op)

        self.assertEqual(previsto['materia_prima'], Decimal('200.0000'))
        self.assertEqual(previsto['embalagem'], Decimal('500.0000'))

    def test_o_previsto_traz_processo_da_ficha(self):
        op = self._op()

        previsto = CustoService.previsto(op)

        self.assertEqual(previsto['mao_de_obra'], Decimal('300.0000'))
        self.assertEqual(previsto['indireto'], Decimal('100.0000'))

    def test_o_previsto_escala_com_o_tamanho_da_batida(self):
        """
        A receita é escrita para 1.000 unidades; a ordem pode ser de 300. Sem
        o fator, a comparação acusaria desvio onde não houve.
        """
        op = self._op(quantidade='500')

        previsto = CustoService.previsto(op)

        self.assertEqual(previsto['materia_prima'], Decimal('100.0000'))
        self.assertEqual(previsto['mao_de_obra'], Decimal('150.0000'))

    def test_perda_nao_entra_no_previsto(self):
        """
        A perda esperada já está dentro da matéria-prima — a receita compra
        1.000 kg para render 600. Somá-la de novo contaria a mesma fruta duas
        vezes.
        """
        op = self._op()

        self.assertEqual(CustoService.previsto(op)['perdas'], ZERO)

    def test_as_quatro_divisoes(self):
        op = self._op()

        previsto = CustoService.previsto(op)

        # 1.100 no total, 1.000 unidades, 100 kg (0,1 kg cada), caixa de 50.
        self.assertEqual(previsto['total'], Decimal('1100.0000'))
        self.assertEqual(previsto['por_unidade'], Decimal('1.1000'))
        self.assertEqual(previsto['por_kg'], Decimal('11.0000'))
        self.assertEqual(previsto['por_caixa'], Decimal('55.0000'))


class CustosConfiguraveisTests(CustoBase):

    def test_custo_por_batida_nao_muda_com_o_tamanho(self):
        """Setup de linha e higienização custam o mesmo em 100 ou 1.000 kg."""
        CustoReceita.objects.create(
            filial=self.filial, receita=self.receita,
            nome='Higienização da linha', valor=Decimal('80'), base=B.BATIDA,
        )

        cheia = CustoService.previsto(self._op(quantidade='1000'))
        self.assertEqual(cheia['extras'], Decimal('80.0000'))

    def test_custo_por_quilo_acompanha_o_peso(self):
        """Energia do túnel: congelar o dobro custa o dobro."""
        CustoReceita.objects.create(
            filial=self.filial, receita=self.receita,
            nome='Energia do túnel', valor=Decimal('3'), base=B.KG,
        )
        op = self._op(quantidade='1000')  # 1.000 × 0,1 kg = 100 kg

        self.assertEqual(
            CustoService.previsto(op)['extras'], Decimal('300.0000'),
        )

    def test_custo_por_unidade_acompanha_a_contagem(self):
        CustoReceita.objects.create(
            filial=self.filial, receita=self.receita,
            nome='Rótulo', valor=Decimal('0.05'), base=B.UNIDADE,
        )
        op = self._op(quantidade='1000')

        self.assertEqual(
            CustoService.previsto(op)['extras'], Decimal('50.0000'),
        )

    def test_custo_inativo_fica_de_fora(self):
        CustoReceita.objects.create(
            filial=self.filial, receita=self.receita,
            nome='Energia', valor=Decimal('100'), base=B.BATIDA, ativo=False,
        )

        self.assertEqual(CustoService.previsto(self._op())['extras'], ZERO)

    def test_custo_por_kg_sem_peso_nao_infla_a_batida(self):
        """
        Um custo por quilo numa receita sem peso não é "o valor cheio" — é uma
        conta que não dá para fazer, e assumi-la cheia inflaria o lote sem que
        ninguém percebesse de onde veio.
        """
        custo = CustoReceita(
            filial=self.filial, receita=self.receita,
            nome='Energia', valor=Decimal('3'), base=B.KG,
        )

        self.assertEqual(custo.total_para(Decimal('100'), ZERO), ZERO)


class RealizadoTests(CustoBase):

    def test_o_realizado_sai_do_lote_consumido(self):
        """
        Não do custo médio de hoje: a manga da semana passada custou o que
        custou, e recalcular pela média reescreveria o custo de um produto
        que já foi vendido.
        """
        op = self._op(custo_manga='3')   # lote mais caro que o cadastro (2)
        self._produzir(op)

        realizado = CustoService.realizado(op)

        self.assertEqual(realizado['materia_prima'], Decimal('300.0000'))

    def test_fruta_e_pote_saem_separados(self):
        """
        `op_service` soma os dois em `custo_materia_prima`. Comparar essa soma
        com um previsto que separa acusaria a categoria errada.
        """
        op = self._op()
        self._produzir(op)

        realizado = CustoService.realizado(op)

        self.assertEqual(realizado['materia_prima'], Decimal('200.0000'))
        self.assertEqual(realizado['embalagem'], Decimal('500.0000'))

    def test_ordem_nao_concluida_nao_tem_total(self):
        """
        Zero seria lido como "não custou nada", e uma ordem em andamento
        apareceria como a mais barata da lista.
        """
        op = self._op()

        realizado = CustoService.realizado(op)

        self.assertIsNone(realizado['total'])
        self.assertIsNone(realizado['por_kg'])

    def test_o_realizado_tem_as_quatro_divisoes(self):
        op = self._op()
        self._produzir(op)

        realizado = CustoService.realizado(op)

        self.assertIsNotNone(realizado['por_unidade'])
        self.assertIsNotNone(realizado['por_kg'])
        self.assertIsNotNone(realizado['por_caixa'])


class ComparacaoTests(CustoBase):

    def test_sem_desvio_quando_tudo_sai_como_previsto(self):
        op = self._op()
        self._produzir(op)

        comparacao = CustoService.comparar(op)

        self.assertEqual(comparacao['desvio_total'], ZERO)
        self.assertTrue(comparacao['concluida'])

    def test_o_desvio_aponta_a_categoria(self):
        """
        "Custo 8% acima" não diz o que fazer; "matéria-prima 50% acima" manda
        olhar a compra de fruta.
        """
        op = self._op(custo_manga='3')  # 50% acima do cadastro
        self._produzir(op)

        linhas = {l['chave']: l for l in CustoService.comparar(op)['linhas']}

        self.assertEqual(linhas['materia_prima']['desvio'], Decimal('100.0000'))
        self.assertEqual(
            linhas['materia_prima']['desvio_percentual'], Decimal('50.0000'),
        )
        self.assertTrue(linhas['materia_prima']['acima'])
        self.assertEqual(linhas['embalagem']['desvio'], ZERO)

    def test_desvio_percentual_e_nulo_quando_o_previsto_e_zero(self):
        """
        Dividir por zero não é "infinito por cento": é uma conta que não
        existe, e um número ali faria alguém explicar um desvio inventado.
        """
        op = self._op()
        self._produzir(op)

        linhas = {l['chave']: l for l in CustoService.comparar(op)['linhas']}

        self.assertIsNone(linhas['perdas']['desvio_percentual'])

    def test_ordem_aberta_compara_sem_total_realizado(self):
        op = self._op()

        comparacao = CustoService.comparar(op)

        self.assertFalse(comparacao['concluida'])
        self.assertIsNone(comparacao['desvio_total'])
        self.assertEqual(comparacao['previsto']['total'], Decimal('1100.0000'))

    def test_todas_as_categorias_da_especificacao_aparecem(self):
        op = self._op()

        chaves = {l['chave'] for l in CustoService.comparar(op)['linhas']}

        self.assertEqual(chaves, {
            'materia_prima', 'embalagem', 'mao_de_obra',
            'indireto', 'extras', 'perdas',
        })


class TelaTests(CustoBase):
    """O número solto virou quadro comparativo."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def _abrir(self, op):
        from django.urls import reverse
        return self.client.get(reverse('polpa:ordem-detail', args=[op.pk]))

    def test_a_tela_mostra_as_categorias_da_especificacao(self):
        op = self._op()

        resposta = self._abrir(op)

        self.assertEqual(resposta.status_code, 200)
        for rotulo in ('Matéria-prima', 'Embalagem', 'Mão de obra',
                       'Custos indiretos', 'Energia e outros', 'Perdas'):
            self.assertContains(resposta, rotulo)

    def test_ordem_aberta_nao_mostra_coluna_de_realizado(self):
        """
        Zero na coluna do realizado faria a ordem parecer a mais barata da
        fábrica — e é assim que alguém fecha preço olhando custo que ainda não
        existe.
        """
        op = self._op()

        resposta = self._abrir(op)

        self.assertContains(resposta, 'o realizado aparece quando a batida fechar')
        self.assertNotContains(resposta, '<th class="text-right font-medium pb-2">Realizado</th>')

    def test_ordem_concluida_mostra_a_comparacao(self):
        op = self._op()
        self._produzir(op)

        resposta = self._abrir(op)

        self.assertContains(resposta, 'Realizado')
        self.assertContains(resposta, 'Desvio')

    def test_a_tela_mostra_as_quatro_divisoes(self):
        op = self._op()

        resposta = self._abrir(op)

        self.assertContains(resposta, 'Por unidade')
        self.assertContains(resposta, 'Por kg')
        self.assertContains(resposta, 'Por caixa')

    def test_nada_de_tag_vaza_para_a_tela(self):
        op = self._op()
        self._produzir(op)

        corpo = self._abrir(op).content.decode()

        for marca in ('{%', '%}', '{#', '#}', 'endcomment'):
            self.assertNotIn(marca, corpo, f'tag {marca} vazou para a tela')
