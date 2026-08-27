"""
A resolução de CFOP por natureza de operação.

É a peça que tira o fiscal de dentro do código. Se ela erra, a nota sai com o
CFOP errado — e isso só aparece na apuração, quando já custa carta de correção
ou cancelamento fora do prazo.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.core.models import Empresa, Filial
from apps.core.services.exceptions import DadosInvalidosError
from apps.cadastros.models import Cliente
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.fiscal.services.natureza_operacao_service import (
    ContextoFiscal, NaturezaOperacaoService,
)
from apps.produtos.models import Produto
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial


class ResolucaoDeNaturezaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Fiscal LTDA', nome_fantasia='Fiscal',
            cnpj='63345678000191', segmento='polpa_frutas',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='63345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        cls.produto = Produto.objects.create(
            filial=cls.filial, unidade_medida=cls.unidade,
            descricao='Polpa de Manga', codigo='PM1', ncm='20079900',
        )
        cls.cliente_rn = Cliente.objects.create(
            filial=cls.filial, razao_social='Cliente RN', cpf_cnpj='12345678901', uf='RN',
        )
        cls.cliente_pb = Cliente.objects.create(
            filial=cls.filial, razao_social='Cliente PB', cpf_cnpj='12345678902', uf='PB',
        )

    def _natureza(self, codigo='bonificacao', especie=NaturezaOperacao.Especie.BONIFICACAO):
        return NaturezaOperacao.objects.create(
            filial=self.filial, codigo=codigo, descricao=f'Natureza {codigo}',
            especie=especie,
        )

    def _regra(self, natureza, cfop, **kwargs):
        return RegraNaturezaOperacao.objects.create(
            natureza=natureza, cfop=cfop, **kwargs,
        )

    def _contexto(self, cliente=None, produto=None, data=None):
        return NaturezaOperacaoService.contexto_da_operacao(
            filial=self.filial, cliente=cliente,
            produto=produto or self.produto, data=data,
        )

    # ── O caminho comum ──────────────────────────────────────────────────

    def test_a_regra_padrao_vale_quando_nao_ha_outra(self):
        natureza = self._natureza()
        self._regra(natureza, '5910')

        resultado = NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_rn))

        self.assertEqual(resultado.cfop, '5910')
        self.assertEqual(resultado.natureza_operacao, natureza.descricao)

    def test_dentro_e_fora_do_estado_tem_cfop_diferente(self):
        """
        A mesma bonificação é 5910 dentro do estado e 6910 para fora. Sem isso,
        toda venda interestadual sai com o CFOP de operação interna.
        """
        natureza = self._natureza()
        self._regra(natureza, '5910')
        self._regra(natureza, '6910', somente_interestadual=True)

        interna = NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_rn))
        externa = NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_pb))

        self.assertEqual(interna.cfop, '5910')
        self.assertEqual(externa.cfop, '6910')

    def test_a_regra_interestadual_nao_vale_dentro_do_estado(self):
        natureza = self._natureza()
        self._regra(natureza, '6910', somente_interestadual=True)

        with self.assertRaises(DadosInvalidosError):
            NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_rn))

    # ── A mais específica ganha ──────────────────────────────────────────

    def test_a_regra_de_uf_vence_a_geral(self):
        natureza = self._natureza()
        self._regra(natureza, '6910', somente_interestadual=True)
        self._regra(natureza, '6911', uf_destino='PB')

        resultado = NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_pb))

        self.assertEqual(resultado.cfop, '6911')

    def test_a_regra_de_produto_vence_a_de_uf(self):
        """
        Produto com tributação própria é o caso que mais aparece na prática, e
        precisa ganhar de qualquer regra mais larga.
        """
        natureza = self._natureza()
        self._regra(natureza, '5910')
        self._regra(natureza, '5911', produto=self.produto)

        resultado = NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_rn))

        self.assertEqual(resultado.cfop, '5911')

    def test_a_regra_de_outro_produto_nao_contamina(self):
        outro = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade,
            descricao='Caixa', codigo='CX1', ncm='48191000',
        )
        natureza = self._natureza()
        self._regra(natureza, '5910')
        self._regra(natureza, '5911', produto=outro)

        resultado = NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_rn))

        self.assertEqual(resultado.cfop, '5910')

    def test_a_regra_de_regime_so_vale_para_aquele_regime(self):
        natureza = self._natureza()
        self._regra(natureza, '5910')
        self._regra(natureza, '5912', regime_tributario=Empresa.RegimeTributario.LUCRO_REAL)

        resultado = NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_rn))

        self.assertEqual(resultado.cfop, '5910', 'pegou a regra de outro regime')

    def test_a_regra_de_ncm_vale_para_o_ncm_do_produto(self):
        natureza = self._natureza()
        self._regra(natureza, '5910')
        self._regra(natureza, '5913', ncm='20079900')

        resultado = NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_rn))

        self.assertEqual(resultado.cfop, '5913')

    # ── Vigência ─────────────────────────────────────────────────────────

    def test_regra_que_ainda_nao_comecou_nao_vale(self):
        natureza = self._natureza()
        self._regra(natureza, '5910')
        self._regra(natureza, '5999', uf_destino='RN',
                    vigencia_inicio=date.today() + timedelta(days=30))

        resultado = NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_rn))

        self.assertEqual(resultado.cfop, '5910')

    def test_regra_vencida_nao_vale(self):
        natureza = self._natureza()
        self._regra(natureza, '5910')
        self._regra(natureza, '5998', uf_destino='RN',
                    vigencia_fim=date.today() - timedelta(days=1))

        resultado = NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_rn))

        self.assertEqual(resultado.cfop, '5910')

    def test_a_nota_de_ontem_continua_explicada_pela_regra_de_ontem(self):
        """
        Mudar a regra nao pode reescrever como a nota ja' emitida devia ter
        saido. E' por isso que a vigencia existe em vez de edicao.
        """
        natureza = self._natureza()
        ontem = date.today() - timedelta(days=1)
        self._regra(natureza, '5910', vigencia_fim=ontem)
        self._regra(natureza, '5920', vigencia_inicio=date.today())

        antiga = NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_rn, data=ontem))
        atual = NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_rn))

        self.assertEqual(antiga.cfop, '5910')
        self.assertEqual(atual.cfop, '5920')

    def test_regra_inativa_nao_vale(self):
        natureza = self._natureza()
        self._regra(natureza, '5910')
        self._regra(natureza, '5997', uf_destino='RN', ativo=False)

        resultado = NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_rn))

        self.assertEqual(resultado.cfop, '5910')

    # ── Sem regra, para ──────────────────────────────────────────────────

    def test_sem_regra_recusa_em_vez_de_chutar(self):
        """
        Nao existe CFOP padrao de emergencia: chutar produz nota autorizada
        errada, que so' aparece na apuracao.
        """
        natureza = self._natureza()

        with self.assertRaises(DadosInvalidosError) as erro:
            NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_rn))

        self.assertIn('Cadastre a regra', str(erro.exception))

    # ── A trilha de decisão ──────────────────────────────────────────────

    def test_o_resultado_diz_por_que_escolheu_aquela_regra(self):
        """
        Sem isto, "por que saiu 6910 nesta nota?" vira arqueologia de banco
        seis meses depois.
        """
        natureza = self._natureza()
        regra = self._regra(natureza, '6910', somente_interestadual=True)

        resultado = NaturezaOperacaoService.resolver(natureza, self._contexto(self.cliente_pb))

        self.assertEqual(resultado.regra_id, regra.pk)
        self.assertTrue(resultado.justificativa['interestadual'])
        self.assertEqual(resultado.justificativa['uf_destino'], 'PB')

    # ── Sem destinatário ─────────────────────────────────────────────────

    def test_remessa_sem_destinatario_e_operacao_interna(self):
        """
        A remessa para venda fora sai sem comprador: a nota e' contra a propria
        empresa, e nao atravessa fronteira nenhuma.
        """
        natureza = self._natureza('remessa', NaturezaOperacao.Especie.REMESSA_VENDA_FORA)
        self._regra(natureza, '5904')
        self._regra(natureza, '6904', somente_interestadual=True)

        resultado = NaturezaOperacaoService.resolver(natureza, self._contexto(cliente=None))

        self.assertEqual(resultado.cfop, '5904')

    # ── A natureza por espécie ───────────────────────────────────────────

    def test_a_filial_encontra_a_natureza_da_especie(self):
        natureza = self._natureza('bonif', NaturezaOperacao.Especie.BONIFICACAO)

        achada = NaturezaOperacaoService.por_especie(
            self.filial, NaturezaOperacao.Especie.BONIFICACAO,
        )

        self.assertEqual(achada, natureza)

    def test_sem_natureza_da_especie_o_erro_diz_onde_cadastrar(self):
        with self.assertRaises(DadosInvalidosError) as erro:
            NaturezaOperacaoService.por_especie(
                self.filial, NaturezaOperacao.Especie.BONIFICACAO,
            )

        self.assertIn('Naturezas de operação', str(erro.exception))

    def test_com_duas_naturezas_da_mesma_especie_a_escolha_e_de_quem_monta(self):
        self._natureza('bonif_a', NaturezaOperacao.Especie.BONIFICACAO)
        self._natureza('bonif_b', NaturezaOperacao.Especie.BONIFICACAO)

        with self.assertRaises(DadosInvalidosError) as erro:
            NaturezaOperacaoService.por_especie(
                self.filial, NaturezaOperacao.Especie.BONIFICACAO,
            )

        self.assertIn('escolha', str(erro.exception).lower())
