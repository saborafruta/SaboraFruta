"""
Estrutura do produto — o que precisa estar certo é o CICLO.

Nada no banco impede gravar A dentro de B e B dentro de A, e a partir daí
qualquer leitura da árvore roda para sempre. O serviço barra na gravação e
corta na leitura; estes testes cobrem os dois caminhos, porque linha criada
por importação ou por shell nunca passou pela view.
"""
from decimal import Decimal

from django.test import TestCase

from apps.core.models import Empresa, Filial
from apps.moda.models import EstruturaProduto, ProdutoModa
from apps.moda.services import estrutura as servico


class EstruturaProdutoTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Teste LTDA', nome_fantasia='Confeccao Teste',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Teste LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.outra_filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Outra Filial LTDA',
            cnpj='53345678000353', uf='PB', cidade='Joao Pessoa',
        )

        cls.conjunto = cls._produto('CONJ001', 'Conjunto de linha')
        cls.camisa = cls._produto('CAM001', 'Camisa de jogo')
        cls.calcao = cls._produto('CAL001', 'Calcao de jogo')
        cls.meiao = cls._produto('MEI001', 'Meiao')

    @classmethod
    def _produto(cls, codigo, nome, filial=None):
        return ProdutoModa.objects.create(
            filial=filial or cls.filial, codigo=codigo, nome=nome,
        )

    # ── Inclusão ─────────────────────────────────────────────────────────

    def test_inclui_componente_simples(self):
        pode, motivo = servico.pode_incluir(self.conjunto, self.camisa)
        self.assertTrue(pode, motivo)

    def test_produto_nao_pode_ser_componente_de_si_mesmo(self):
        pode, motivo = servico.pode_incluir(self.conjunto, self.conjunto)
        self.assertFalse(pode)
        self.assertIn('si mesmo', motivo)

    def test_recusa_componente_repetido(self):
        EstruturaProduto.objects.create(pai=self.conjunto, componente=self.camisa)
        pode, motivo = servico.pode_incluir(self.conjunto, self.camisa)
        self.assertFalse(pode)
        self.assertIn('quantidade', motivo)

    def test_recusa_componente_de_outra_filial(self):
        forasteiro = self._produto('OUT001', 'Camisa de outra filial', self.outra_filial)
        pode, motivo = servico.pode_incluir(self.conjunto, forasteiro)
        self.assertFalse(pode)
        self.assertIn('mesma filial', motivo)

    # ── Ciclo ────────────────────────────────────────────────────────────

    def test_recusa_ciclo_direto(self):
        """A dentro de B, e B dentro de A."""
        EstruturaProduto.objects.create(pai=self.conjunto, componente=self.camisa)
        pode, motivo = servico.pode_incluir(self.camisa, self.conjunto)
        self.assertFalse(pode)
        self.assertIn('ciclo', motivo.lower())

    def test_recusa_ciclo_indireto(self):
        """A → B → C, e tentar pôr A dentro de C."""
        EstruturaProduto.objects.create(pai=self.conjunto, componente=self.camisa)
        EstruturaProduto.objects.create(pai=self.camisa, componente=self.calcao)
        pode, motivo = servico.pode_incluir(self.calcao, self.conjunto)
        self.assertFalse(pode)
        self.assertIn('ciclo', motivo.lower())

    def test_arvore_nao_trava_com_ciclo_gravado_por_fora(self):
        """
        Ciclo criado sem passar pela view — por importação ou shell.

        A leitura tem de terminar mesmo assim, e marcar o nó em vez de
        desaparecer com ele.
        """
        EstruturaProduto.objects.create(pai=self.conjunto, componente=self.camisa)
        EstruturaProduto.objects.create(pai=self.camisa, componente=self.conjunto)

        linhas = servico.arvore(self.conjunto)

        self.assertTrue(any(n['ciclo'] for n in linhas), 'o no repetido devia vir marcado')
        self.assertLessEqual(len(linhas), 10, 'a leitura nao pode explodir em profundidade')

    # ── Quantidades e custo ──────────────────────────────────────────────

    def test_quantidade_multiplica_pela_cadeia(self):
        """Dois conjuntos de duas camisas dão quatro camisas na ponta."""
        EstruturaProduto.objects.create(
            pai=self.conjunto, componente=self.camisa, quantidade=Decimal('2'),
        )
        EstruturaProduto.objects.create(
            pai=self.camisa, componente=self.meiao, quantidade=Decimal('3'),
        )

        linhas = {n['produto'].codigo: n for n in servico.arvore(self.conjunto)}

        self.assertEqual(linhas['CAM001']['quantidade_total'], Decimal('2'))
        # 2 camisas × 3 meiões cada
        self.assertEqual(linhas['MEI001']['quantidade_total'], Decimal('6'))

    def test_niveis_saem_na_ordem_de_profundidade(self):
        EstruturaProduto.objects.create(pai=self.conjunto, componente=self.camisa)
        EstruturaProduto.objects.create(pai=self.camisa, componente=self.meiao)

        linhas = servico.arvore(self.conjunto)

        self.assertEqual([n['produto'].codigo for n in linhas], ['CAM001', 'MEI001'])
        self.assertEqual([n['nivel'] for n in linhas], [1, 2])

    def test_custo_sem_ficha_e_zero_e_nao_erro(self):
        """Conjunto sem ficha própria é o caso normal: tudo vem das partes."""
        EstruturaProduto.objects.create(pai=self.conjunto, componente=self.camisa)
        self.assertEqual(servico.custo_estrutura(self.conjunto), Decimal('0.00'))
