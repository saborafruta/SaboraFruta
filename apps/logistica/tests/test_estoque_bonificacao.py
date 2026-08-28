"""
O estoque da bonificação, e o histórico que ela deixa.

O EXEMPLO DA ESPECIFICAÇÃO: estoque 1.000, bonificação 20, saldo 980.

O QUE ESTES TESTES CERCAM:

  · A BONIFICAÇÃO DA CARGA BAIXA O ESTOQUE quando a carga fecha, com
    movimento próprio no razão — tipo "bonificação", não "saída";

  · A DA RUA NÃO BAIXA DE NOVO. A mercadoria saiu antes, amparada pela
    remessa; baixar aqui tiraria a mesma caixa duas vezes. O que ela consome
    é o saldo em poder da viagem — e a tela DIZ isso, senão quem confere vê
    cortesia "sem baixa" e conclui que o sistema perdeu movimento;

  · O RAZÃO PASSA A DIZER PARA QUEM. Sem o destinatário, duas bonificações
    na mesma viagem viram dois movimentos indistinguíveis;

  · O RÓTULO É A OPERAÇÃO. Quem abre o extrato do produto lê "Saída por
    Bonificação" e entende sem abrir a viagem;

  · A NOTA CHEGA DEPOIS DA BAIXA, e é ligada ao movimento quando é emitida —
    mas só quando o vínculo é inequívoco.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import VendaViagem, Viagem
from apps.logistica.services.historico_bonificacao import (
    HistoricoBonificacaoService,
)
from apps.logistica.services.remessa_nfe import RemessaVendaForaService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

ZERO = Decimal('0')
T = VendaViagem.Tipo
M = VendaViagem.Motivo


class EstoqueBonificacaoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Brinde Estoque LTDA', nome_fantasia='Brinde',
            cnpj='71345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='71345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='doca@brinde.local', nome='Doca', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.outro = Cliente.objects.create(
            filial=cls.filial, razao_social='Padaria do Bairro',
            cpf_cnpj='98765432100', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.outro, filial=cls.filial)

        cls.bonificacao = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='bonif', descricao='Bonificação',
            especie=NaturezaOperacao.Especie.BONIFICACAO,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.bonificacao, cfop='5910')
        cls.remessa = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='remessa', descricao='Remessa venda fora',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.remessa, cfop='5904')

    def setUp(self):
        self.client.force_login(self.usuario)
        self.produto = self._produto('P1', '1000')

    # ── Fixtures ─────────────────────────────────────────────────────────

    def _produto(self, codigo, saldo):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade,
            descricao=f'Produto {codigo}', codigo=codigo, ncm='20079900',
            controla_lote=False, preco_venda=Decimal('10'),
            preco_custo=Decimal('4'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        MovimentacaoService.registrar_movimentacao(
            produto_id=produto.pk, filial_id=self.filial.pk,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
            quantidade=Decimal(saldo), usuario_id=self.usuario.pk,
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
        )
        return produto

    def _viagem(self, numero=None):
        return Viagem.objects.create(
            filial=self.filial, numero=numero or (Viagem.objects.count() + 1),
            motorista_nome='Seu Zé', veiculo_placa='ABC1D23',
            vendedor=self.usuario, responsavel=self.usuario,
        )

    def _com_bonificacao_na_carga(self, quantidade='20', cliente=None):
        viagem = self._viagem()
        ViagemService.adicionar_item(viagem, {
            'natureza': self.bonificacao, 'produto': self.produto,
            'cliente': cliente or self.cliente,
            'quantidade': quantidade, 'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        return viagem

    def _com_remessa_e_bonificacao_na_rua(self, remetido='300', dado='20'):
        viagem = self._viagem()
        ViagemService.adicionar_item(viagem, {
            'natureza': self.remessa, 'produto': self.produto,
            'quantidade': remetido, 'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])
        VendaViagemService.registrar(viagem, {
            'tipo': T.BONIFICACAO, 'produto': self.produto,
            'quantidade': dado, 'valor_unitario': '10',
            'cliente': self.cliente, 'motivo': M.CAMPANHA,
        }, usuario=self.usuario)
        return viagem

    def _saldo(self) -> Decimal:
        return Estoque.objects.get(
            produto=self.produto, filial=self.filial,
        ).quantidade_atual

    def _movimento_de_bonificacao(self):
        return MovimentacaoEstoque.objects.filter(
            produto=self.produto,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.BONIFICACAO,
        ).first()


class BaixaTests(EstoqueBonificacaoBase):
    """O exemplo da especificação."""

    def test_mil_menos_vinte_bonificadas_deixa_novecentos_e_oitenta(self):
        self.assertEqual(self._saldo(), Decimal('1000.000'))

        self._com_bonificacao_na_carga('20')

        self.assertEqual(self._saldo(), Decimal('980.000'))

    def test_a_saida_e_do_tipo_bonificacao_e_nao_venda(self):
        """
        Tipo "saída" no razão faria a cortesia se confundir com venda em
        qualquer relatório que agrupe por operação.
        """
        self._com_bonificacao_na_carga('20')

        movimento = self._movimento_de_bonificacao()

        self.assertIsNotNone(movimento)
        self.assertEqual(movimento.quantidade, Decimal('20.000'))
        self.assertEqual(movimento.quantidade_anterior, Decimal('1000.000'))
        self.assertEqual(movimento.quantidade_posterior, Decimal('980.000'))

    def test_a_bonificacao_da_rua_nao_baixa_o_estoque_de_novo(self):
        """
        A mercadoria saiu na remessa: baixar aqui tiraria a mesma caixa duas
        vezes.
        """
        self._com_remessa_e_bonificacao_na_rua(remetido='300', dado='20')

        # 1.000 - 300 da remessa. A bonificação de 20 consumiu o saldo da
        # viagem, não o estoque da filial.
        self.assertEqual(self._saldo(), Decimal('700.000'))
        self.assertIsNone(self._movimento_de_bonificacao())


class HistoricoTests(EstoqueBonificacaoBase):
    """Os vínculos que o histórico precisa carregar."""

    def test_o_movimento_diz_para_quem_a_mercadoria_saiu(self):
        """
        Sem o destinatário, duas bonificações na mesma viagem viram dois
        movimentos indistinguíveis.
        """
        self._com_bonificacao_na_carga('20', cliente=self.outro)

        self.assertEqual(self._movimento_de_bonificacao().cliente, self.outro)

    def test_o_rotulo_diz_a_operacao(self):
        """Movimento que só se explica em outra tela ninguém confere."""
        self._com_bonificacao_na_carga('20')

        self.assertIn(
            'Saída por Bonificação', self._movimento_de_bonificacao().observacao,
        )

    def test_o_movimento_guarda_viagem_produto_usuario_e_hora(self):
        viagem = self._com_bonificacao_na_carga('20')

        movimento = self._movimento_de_bonificacao()

        self.assertEqual(movimento.documento_tipo, 'viagem')
        self.assertEqual(movimento.documento_id, viagem.pk)
        self.assertEqual(movimento.produto, self.produto)
        self.assertEqual(movimento.usuario, self.usuario)
        self.assertIsNotNone(movimento.data_movimentacao)

    def test_a_nota_e_ligada_ao_movimento_quando_e_emitida(self):
        """
        A baixa acontece antes da nota — sem este passo o extrato mostra a
        saída para sempre sem dizer sob qual documento ela foi.
        """
        viagem = self._viagem()
        ViagemService.adicionar_item(viagem, {
            'natureza': self.remessa, 'produto': self.produto,
            'quantidade': '300', 'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)

        documento = RemessaVendaForaService.emitir(viagem, self.usuario)

        movimento = MovimentacaoEstoque.objects.filter(
            documento_tipo='viagem', documento_id=viagem.pk,
        ).first()
        self.assertEqual(movimento.documento_fiscal, documento)

    def test_o_vinculo_ambiguo_fica_vazio_em_vez_de_chutado(self):
        """
        Duas linhas do mesmo produto e lote em naturezas diferentes geram
        movimentos indistinguíveis — chutar poria a remessa amparando uma
        bonificação.
        """
        viagem = self._viagem()
        ViagemService.adicionar_item(viagem, {
            'natureza': self.remessa, 'produto': self.produto,
            'quantidade': '300', 'valor_unitario': '10',
        })
        ViagemService.adicionar_item(viagem, {
            'natureza': self.bonificacao, 'produto': self.produto,
            'cliente': self.cliente, 'quantidade': '20', 'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)

        RemessaVendaForaService.emitir(viagem, self.usuario)

        semvinculo = MovimentacaoEstoque.objects.filter(
            documento_tipo='viagem', documento_id=viagem.pk,
            documento_fiscal__isnull=True,
        )
        self.assertEqual(semvinculo.count(), 2)


class LeituraTests(EstoqueBonificacaoBase):
    """As duas bonificações lidas juntas, sem confundir a baixa."""

    def test_a_da_carga_aparece_com_a_baixa_no_estoque(self):
        viagem = self._com_bonificacao_na_carga('20')

        linhas = HistoricoBonificacaoService.linhas(viagem)

        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]['origem'], 'carga')
        self.assertEqual(linhas[0]['saldo_apos'], Decimal('980.000'))
        self.assertEqual(linhas[0]['cliente'], self.cliente)

    def test_a_da_rua_aparece_dizendo_onde_a_baixa_aconteceu(self):
        """
        Sem isso, quem confere vê cortesia "sem baixa" e conclui que o
        sistema perdeu movimento.
        """
        viagem = self._com_remessa_e_bonificacao_na_rua()

        linhas = HistoricoBonificacaoService.linhas(viagem)

        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]['origem'], 'remessa')
        self.assertIsNone(linhas[0]['saldo_apos'])
        self.assertEqual(linhas[0]['motivo'], 'Campanha promocional')

    def test_bonificacao_sem_nota_e_contada_como_pendencia(self):
        viagem = self._com_bonificacao_na_carga('20')

        resumo = HistoricoBonificacaoService.resumo(
            HistoricoBonificacaoService.linhas(viagem),
        )

        self.assertEqual(resumo['sem_nota'], 1)
        self.assertEqual(resumo['quantidade'], Decimal('20.000'))

    def test_venda_nao_entra_no_historico_de_bonificacao(self):
        viagem = self._viagem()
        venda = NaturezaOperacao.objects.create(
            filial=self.filial, codigo='venda', descricao='Venda',
            especie=NaturezaOperacao.Especie.VENDA,
        )
        RegraNaturezaOperacao.objects.create(natureza=venda, cfop='5102')
        ViagemService.adicionar_item(viagem, {
            'natureza': venda, 'produto': self.produto,
            'cliente': self.cliente, 'quantidade': '50', 'valor_unitario': '10',
        })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)

        self.assertEqual(HistoricoBonificacaoService.linhas(viagem), [])


class TelaTests(EstoqueBonificacaoBase):
    """O histórico na tela da viagem."""

    def _detalhe(self, viagem):
        return self.client.get(
            reverse('logistica:viagem-detail', args=[viagem.pk]),
        ).content.decode()

    def test_a_tela_mostra_a_bonificacao_e_a_baixa(self):
        viagem = self._com_bonificacao_na_carga('20')

        html = self._detalhe(viagem)

        self.assertIn('Bonificações desta viagem', html)
        self.assertIn('Saída por Bonificação', html)
        self.assertIn('estoque da filial', html)

    def test_a_tela_explica_a_bonificacao_da_rua(self):
        viagem = self._com_remessa_e_bonificacao_na_rua()

        html = self._detalhe(viagem)

        self.assertIn('saldo da viagem', html)
        self.assertIn('estoque já saiu na remessa', html)
