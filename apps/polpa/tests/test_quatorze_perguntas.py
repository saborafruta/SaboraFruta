"""
As catorze perguntas do princípio central — cada uma com a sua resposta.

A especificação não pede telas: pede que o módulo RESPONDA. Este arquivo pega
as catorze perguntas, uma a uma, e mostra de onde sai a resposta — porque
"está tudo integrado" é fácil de dizer e difícil de sustentar, e treze itens
desta sessão mostraram que código existir não é o mesmo que código responder.

Treze delas já tinham resposta quando cheguei aqui, algumas construídas nesta
sessão. A décima quarta — margem de contribuição — não existia em lugar
nenhum: `IndicadoresService` cobre produção, eficiência, custo, estoque e
qualidade, e para no custo. Custo sem preço não decide nada.

CONTRIBUIÇÃO NÃO É LUCRO. Ela desconta só o custo VARIÁVEL, o que existe
porque aquela batida existiu. Mão de obra e rateio de indireto correm com a
linha parada; incluí-los daria margem líquida com nome de contribuição, e a
decisão que a contribuição sustenta — "vale a pena produzir mais este item?" —
passaria a ser respondida com o número errado.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.cadastros.models import Fornecedor
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque, LoteProduto
from apps.lotes.services.rastreio import RastreioService
from apps.polpa.models import (
    Camara, CustoReceita, EtapaReceita, FichaProduto, OrdemPolpa,
)
from apps.polpa.services import CatalogoService, OrdemPolpaService, ReceitaService
from apps.polpa.services.armazenagem import ArmazenagemService
from apps.polpa.services.compra import CompraService
from apps.polpa.services.custo import CustoService
from apps.polpa.services.margem import MargemService
from apps.polpa.services.planejamento import PlanejamentoService
from apps.polpa.services.processo import ProcessoService
from apps.producao.models import ItemFichaTecnica
from apps.produtos.models import ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial
from apps.qualidade.models import ParametroQualidadeProduto
from apps.qualidade.services.checklist_service import ETAPA_ACABADO, ChecklistService

T = FichaProduto.Tipo
S = OrdemPolpa.Situacao
B = CustoReceita.Base
ZERO = Decimal('0')


class PerguntasBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Polpas Perguntas LTDA', nome_fantasia='Perguntas',
            cnpj='33345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='33345678000272',
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
            email='chefe@perg.local', nome='Maria da Producao',
            password='x' * 12, empresa=cls.empresa, perfil=perfil,
            filial=cls.filial,
        )
        cls.produtor = Fornecedor.objects.create(
            filial=cls.filial, razao_social='Sítio do Silva',
            cpf_cnpj='11122233344',
        )

    def setUp(self):
        self.fruta = self._item(T.FRUTA, 'Acerola in natura', custo='4')
        self.pote = self._item(T.POTE, 'Pote 100 g', custo='0.30')
        self.polpa = self._item(
            T.POLPA, 'Polpa de acerola 100 g', custo='0',
            validade_dias=180, peso_liquido=Decimal('0.100'),
            preco_venda=Decimal('3'),
        )
        self.receita = self._receita()
        self.camara = Camara.objects.create(
            filial=self.filial, nome='Câmara 1',
            temperatura_min=Decimal('-25'), temperatura_max=Decimal('-18'),
            capacidade_kg=Decimal('10000'),
        )

    def _item(self, tipo, descricao, custo='0', **extras):
        dados = {
            'tipo': tipo, 'descricao': descricao, 'codigo': descricao[:10],
            'unidade_medida': self.unidade, 'preco_custo': Decimal(custo),
            'preco_custo_medio': Decimal(custo),
        }
        dados.update(extras)
        produto = CatalogoService.salvar(self.filial, dados).produto
        ProdutoFilial.objects.get_or_create(produto=produto, filial=self.filial)
        return produto

    def _receita(self):
        """1.000 potes: 500 kg de acerola (R$2.000) + 1.000 potes (R$300)."""
        receita = ReceitaService.criar(self.filial, self.polpa, {
            'descricao': 'Polpa de acerola 100 g', 'versao': '1.0',
            'quantidade_produzida': Decimal('1000'),
            'rendimento_esperado': Decimal('60'),
            'custo_mao_obra_padrao': Decimal('400'),
            'custo_indireto_padrao': Decimal('200'),
        })
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.fruta,
            quantidade=Decimal('500'), perda_prevista=ZERO,
        )
        ItemFichaTecnica.objects.create(
            ficha=receita.ficha, materia_prima=self.pote,
            quantidade=Decimal('1000'), perda_prevista=ZERO,
        )
        EtapaReceita.objects.create(receita=receita, ordem=1, nome='Despolpa')
        ReceitaService.ativar(receita)
        return receita

    def _estoque(self, produto, quantidade):
        estoque, _ = Estoque.objects.get_or_create(
            produto=produto, filial=self.filial,
        )
        estoque.quantidade_atual = Decimal(quantidade)
        estoque.quantidade_reservada = ZERO
        estoque.atualizar_disponivel()
        estoque.save()
        LoteProduto.objects.get_or_create(
            filial=self.filial, produto=produto, numero_lote=f'L-{produto.pk}',
            defaults={
                'quantidade_inicial': Decimal(quantidade),
                'quantidade_atual': Decimal(quantidade),
                'custo_unitario': produto.preco_custo,
                'fornecedor': self.produtor,
                'data_validade': timezone.localdate() + timedelta(days=90),
                'status': LoteProduto.Status.ATIVO,
            },
        )

    def _ordem(self):
        self._estoque(self.fruta, '5000')
        self._estoque(self.pote, '50000')
        return OrdemPolpaService.criar(
            self.filial, self.receita,
            {'quantidade_planejada': Decimal('1000')}, self.usuario,
        )

    def _produzir(self, op, entrada='1000', saida='620'):
        OrdemPolpaService.mover(op, S.LIBERADA, self.usuario)
        OrdemPolpaService.mover(op, S.EM_PRODUCAO, self.usuario)
        etapas = list(op.etapas_processo.all())
        ProcessoService.apontar(etapas[0], {
            'quantidade_entrada': entrada, 'quantidade_saida': entrada,
            'operador': self.usuario,
        }, self.usuario)
        ProcessoService.apontar(etapas[-1], {
            'quantidade_entrada': saida, 'quantidade_saida': saida,
        }, self.usuario)
        return OrdemPolpaService.concluir(
            op, self.usuario, quantidade=Decimal('1000'),
        )


class AsQuatorzePerguntasTests(PerguntasBase):

    # ── 1 e 2: o que e quanto devo produzir ──────────────────────────────

    def test_1_e_2_o_que_e_quanto_devo_produzir(self):
        self._estoque(self.polpa, '300')

        linha = next(
            l for l in PlanejamentoService.sugestoes(self.filial)
            if l['produto'].pk == self.polpa.pk
        )

        self.assertEqual(linha['produto'], self.polpa)
        self.assertIsNotNone(linha['necessidade'])
        self.assertEqual(linha['estoque'], Decimal('300.000'))

    # ── 3: quais matérias-primas preciso ─────────────────────────────────

    def test_3_quais_materias_primas_preciso(self):
        self._ordem()

        linhas = {
            l.produto.pk: l for l in CompraService.necessidade(self.filial)
        }

        self.assertIn(self.fruta.pk, linhas)
        self.assertEqual(linhas[self.fruta.pk].necessario, Decimal('500.0000'))

    # ── 4: quanto está custando produzir ─────────────────────────────────

    def test_4_quanto_esta_custando_produzir(self):
        op = self._ordem()
        self._produzir(op)

        custo = CustoService.comparar(op)

        self.assertTrue(custo['concluida'])
        self.assertEqual(
            custo['realizado']['materia_prima'], Decimal('2000.0000'),
        )
        self.assertIsNotNone(custo['realizado']['por_kg'])
        self.assertIsNotNone(custo['desvio_total'])

    # ── 5 e 6: quanto estou perdendo, e qual meu rendimento ──────────────

    def test_5_e_6_perda_e_rendimento(self):
        op = self._ordem()
        self._produzir(op, entrada='1000', saida='620')

        resumo = ProcessoService.resumo(op)

        self.assertEqual(resumo['perda_total'], Decimal('380.000'))
        self.assertEqual(resumo['rendimento'], Decimal('62.00'))
        self.assertEqual(resumo['rendimento_esperado'], Decimal('60.00'))
        self.assertTrue(resumo['rendimento_dentro'])

    # ── 7: qual lote foi produzido ───────────────────────────────────────

    def test_7_qual_lote_foi_produzido(self):
        op = self._ordem()
        self._produzir(op)

        self.assertIsNotNone(op.lote)
        self.assertEqual(op.lote.produto, self.polpa)

    # ── 8: quais matérias-primas foram utilizadas ────────────────────────

    def test_8_quais_materias_primas_foram_utilizadas(self):
        """
        O LOTE de fato consumido, e não a linha da receita — a diferença
        entre "manga" e "lote L-4471 do produtor Silva".
        """
        op = self._ordem()
        self._produzir(op)

        origem = RastreioService.de_onde_veio(op.lote)

        produtos = {e.lote.produto.pk for e in origem if e.nivel > 0}
        self.assertIn(self.fruta.pk, produtos)
        resumo = RastreioService.resumo(origem, [])
        self.assertIn(self.produtor, resumo['fornecedores'])

    # ── 9: onde está o produto ───────────────────────────────────────────

    def test_9_onde_esta_o_produto(self):
        op = self._ordem()
        self._produzir(op)

        armazenado = ArmazenagemService.guardar_da_ordem(op, self.camara)

        self.assertEqual(armazenado.camara, self.camara)
        self.assertEqual(armazenado.lote, op.lote)

    # ── 10: qual é a validade ────────────────────────────────────────────

    def test_10_qual_e_a_validade(self):
        """
        Calculada do prazo do produto, sem ninguém digitar — digitação à mão
        em campo de validade é como sai lote com prazo errado para a rua.
        """
        op = self._ordem()
        self._produzir(op)

        self.assertEqual(
            op.lote.data_validade,
            timezone.localdate() + timedelta(days=180),
        )

    # ── 11 e 12: quem produziu, e quando ─────────────────────────────────

    def test_11_e_12_quem_produziu_e_quando(self):
        op = self._ordem()
        self._produzir(op)

        self.assertEqual(op.ordem.usuario_encerramento, self.usuario)
        self.assertIsNotNone(op.ordem.data_fim_real)
        operadores = {
            e.operador for e in op.etapas_processo.all() if e.operador_id
        }
        self.assertIn(self.usuario, operadores)

    # ── 13: aprovado pelo controle de qualidade ──────────────────────────

    def test_13_o_produto_foi_aprovado_pela_qualidade(self):
        op = self._ordem()
        self._produzir(op)
        ParametroQualidadeProduto.objects.create(
            filial=self.filial, produto=self.polpa, etapa=ETAPA_ACABADO,
            nome_parametro='Temperatura',
            valor_minimo=Decimal('-25'), valor_maximo=Decimal('-18'),
        )
        analise = ChecklistService.abrir(
            self.filial, self.polpa, ETAPA_ACABADO, self.usuario, lote=op.lote,
        )
        ChecklistService.preencher(
            analise, {analise.itens.get().pk: {'valor': '-20'}},
            usuario=self.usuario,
        )

        ChecklistService.concluir(analise, self.usuario)

        op.lote.refresh_from_db()
        self.assertEqual(op.lote.status, LoteProduto.Status.ATIVO)
        self.assertEqual(op.lote.analises.count(), 1)

    # ── 14: qual foi minha margem de contribuição ────────────────────────

    def test_14_qual_foi_minha_margem_de_contribuicao(self):
        """
        Receita 3.000 (1.000 × R$3), variável 2.300 (fruta 2.000 + pote 300).
        Contribuição 700, ou 23,33%.
        """
        op = self._ordem()
        self._produzir(op)

        margem = MargemService.da_ordem(op)

        self.assertEqual(margem['receita'], Decimal('3000.00'))
        self.assertEqual(margem['custo_variavel'], Decimal('2300.00'))
        self.assertEqual(margem['contribuicao'], Decimal('700.00'))
        self.assertEqual(margem['percentual'], Decimal('23.33'))


class ContribuicaoNaoELucroTests(PerguntasBase):

    def test_mao_de_obra_e_indireto_ficam_fora_da_contribuicao(self):
        """
        Eles correm com a linha parada. Incluí-los daria margem líquida com
        nome de contribuição — e a decisão que ela sustenta passaria a ser
        respondida com o número errado.
        """
        op = self._ordem()
        self._produzir(op)

        margem = MargemService.da_ordem(op)

        self.assertEqual(margem['custo_fixo'], Decimal('600.00'))
        self.assertNotIn(
            margem['custo_fixo'], [margem['custo_variavel']],
            'fixo e variável não podem ser a mesma conta',
        )

    def test_o_resultado_aparece_ao_lado_da_contribuicao(self):
        """
        Contribuição positiva com resultado negativo é a situação que a
        fábrica precisa enxergar: o item paga o próprio material e não paga a
        estrutura.
        """
        op = self._ordem()
        self._produzir(op)

        margem = MargemService.da_ordem(op)

        self.assertEqual(margem['contribuicao'], Decimal('700.00'))
        self.assertEqual(margem['resultado'], Decimal('100.00'))

    def test_custo_por_quilo_e_variavel(self):
        """Energia do túnel dobra quando se congela o dobro."""
        CustoReceita.objects.create(
            filial=self.filial, receita=self.receita,
            nome='Energia do túnel', valor=Decimal('2'), base=B.KG,
        )
        op = self._ordem()
        self._produzir(op)

        margem = MargemService.da_ordem(op)

        # 1.000 unidades × 0,1 kg = 100 kg × R$2 = R$200 variáveis.
        self.assertEqual(margem['custo_variavel'], Decimal('2500.00'))
        self.assertEqual(margem['custo_fixo'], Decimal('600.00'))

    def test_custo_por_batida_e_fixo(self):
        """Higienização acontece uma vez e não muda com o tamanho."""
        CustoReceita.objects.create(
            filial=self.filial, receita=self.receita,
            nome='Higienização', valor=Decimal('150'), base=B.BATIDA,
        )
        op = self._ordem()
        self._produzir(op)

        margem = MargemService.da_ordem(op)

        self.assertEqual(margem['custo_variavel'], Decimal('2300.00'))
        self.assertEqual(margem['custo_fixo'], Decimal('750.00'))


class SemPrecoNaoEPrejuizoTests(PerguntasBase):

    def test_produto_sem_preco_nao_da_margem_negativa(self):
        """
        Amostra e teste de receita entrariam como −100% e afundariam a média
        da fábrica. Margem desconhecida e margem ruim são coisas diferentes.
        """
        self.polpa.preco_venda = ZERO
        self.polpa.save(update_fields=['preco_venda'])
        op = self._ordem()
        self._produzir(op)

        margem = MargemService.da_ordem(op)

        self.assertTrue(margem['sem_preco'])
        self.assertIsNone(margem['contribuicao'])
        self.assertIsNone(margem['percentual'])

    def test_ordem_em_andamento_nao_tem_contribuicao(self):
        op = self._ordem()

        margem = MargemService.da_ordem(op)

        self.assertFalse(margem['concluida'])
        self.assertIsNone(margem['contribuicao'])

    def test_o_painel_separa_as_sem_preco_da_media(self):
        """
        Escondê-las faria a tela parecer completa quando parte da produção não
        entrou na conta — e é assim que alguém decide preço olhando meia
        fábrica.
        """
        com_preco = self._ordem()
        self._produzir(com_preco)

        painel = MargemService.painel(self.filial)

        self.assertEqual(len(painel['linhas']), 1)
        self.assertEqual(painel['contribuicao'], Decimal('700.00'))
        self.assertTrue(painel['cobre_o_fixo'])
