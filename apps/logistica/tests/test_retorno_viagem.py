"""
O que deve voltar quando o caminhão chega.

O EXEMPLO DA ESPECIFICAÇÃO: 300 enviadas, 180 vendidas, 120 devem retornar.

O QUE ESTES TESTES CERCAM:

  · O SISTEMA CALCULA, A PESSOA CONFERE. O previsto é o que a conta diz; o
    retorno é o que a contagem física encontra. Aceitar o previsto como fato
    faria sumir exatamente a diferença que importa;

  · A DIVERGÊNCIA VOLTA COMO AVISO, E NÃO COMO BAIXA. Baixa é declaração de
    perda, com responsável — um sistema que a emite sozinho ensina a fábrica
    a não olhar;

  · SÓ O QUE SAIU SEM COMPRADOR volta por aqui: mercadoria vendida saiu
    endereçada, e a devolução dela é outra operação;

  · O RETORNO DEVOLVE AO ESTOQUE, e nunca mais do que está em poder da
    viagem — é o mesmo limite que impede o saldo de fechar negativo.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import Estoque, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import SaldoCarga, VendaViagem, Viagem
from apps.logistica.services.retorno_viagem import RetornoViagemService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

ZERO = Decimal('0')
T = VendaViagem.Tipo


class RetornoViagemBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Volta Carga LTDA', nome_fantasia='Volta',
            cnpj='13945678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='13945678000272',
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
            email='doca@volta.local', nome='Doca', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)
        cls.remessa = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='remessa', descricao='Remessa',
            especie=NaturezaOperacao.Especie.REMESSA_VENDA_FORA,
            exige_destinatario=False,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.remessa, cfop='5904')
        cls.venda = NaturezaOperacao.objects.create(
            filial=cls.filial, codigo='venda', descricao='Venda',
            especie=NaturezaOperacao.Especie.VENDA,
        )
        RegraNaturezaOperacao.objects.create(natureza=cls.venda, cfop='5102')

    def setUp(self):
        self.client.force_login(self.usuario)
        self.produto = self._produto('P1', '5000')

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

    def _viagem(self, remessa='300', venda='0', produto=None):
        viagem = Viagem.objects.create(
            filial=self.filial, numero=Viagem.objects.count() + 1,
            motorista_nome='Seu Zé', veiculo_placa='ABC1D23',
            vendedor=self.usuario, responsavel=self.usuario,
        )
        if Decimal(remessa) > ZERO:
            ViagemService.adicionar_item(viagem, {
                'natureza': self.remessa, 'produto': produto or self.produto,
                'quantidade': remessa, 'valor_unitario': '10',
            })
        if Decimal(venda) > ZERO:
            ViagemService.adicionar_item(viagem, {
                'natureza': self.venda, 'produto': produto or self.produto,
                'cliente': self.cliente, 'quantidade': venda,
                'valor_unitario': '10',
            })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])
        return viagem

    def _vender(self, viagem, quantidade, tipo=T.VENDA, produto=None):
        dados = {
            'tipo': tipo, 'produto': produto or self.produto,
            'quantidade': quantidade, 'valor_unitario': '10',
            'cliente': self.cliente,
        }
        if tipo == T.BONIFICACAO:
            dados['motivo'] = VendaViagem.Motivo.BRINDE
        return VendaViagemService.registrar(viagem, dados, usuario=self.usuario)

    def _saldo_estoque(self, produto=None) -> Decimal:
        return Estoque.objects.get(
            produto=produto or self.produto, filial=self.filial,
        ).quantidade_atual


class CalculoTests(RetornoViagemBase):
    """A conta que o sistema faz sozinho."""

    def test_o_exemplo_da_especificacao(self):
        """300 enviadas, 180 vendidas, 120 devem retornar."""
        viagem = self._viagem(remessa='300')
        self._vender(viagem, '180')

        linhas = RetornoViagemService.previsto(viagem)

        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]['remetida'], Decimal('300.000'))
        self.assertEqual(linhas[0]['vendida'], Decimal('180.000'))
        self.assertEqual(linhas[0]['a_retornar'], Decimal('120.000'))

    def test_a_bonificacao_tambem_reduz_o_que_volta(self):
        viagem = self._viagem(remessa='300')
        self._vender(viagem, '180')
        self._vender(viagem, '20', tipo=T.BONIFICACAO)

        linhas = RetornoViagemService.previsto(viagem)

        self.assertEqual(linhas[0]['bonificada'], Decimal('20.000'))
        self.assertEqual(linhas[0]['a_retornar'], Decimal('100.000'))

    def test_a_linha_mostra_a_conta_inteira(self):
        """
        Quem confere precisa poder verificar de onde saiu o 120 — número que
        não se confere vira ordem, e ordem ninguém contesta quando erra.
        """
        viagem = self._viagem(remessa='300')
        self._vender(viagem, '180')

        linha = RetornoViagemService.previsto(viagem)[0]

        for campo in ('remetida', 'vendida', 'bonificada', 'retornada',
                      'baixada', 'a_retornar'):
            self.assertIn(campo, linha)

    def test_venda_ja_endereçada_nao_entra_no_retorno(self):
        """
        Mercadoria vendida saiu com dono: a devolução dela é outra operação,
        com outro documento.
        """
        viagem = self._viagem(remessa='300', venda='150')

        resumo = RetornoViagemService.resumo(
            RetornoViagemService.previsto(viagem),
        )

        self.assertEqual(resumo['remetida'], Decimal('300.000'))
        self.assertEqual(resumo['a_retornar'], Decimal('300.000'))

    def test_viagem_sem_remessa_nao_tem_o_que_retornar(self):
        viagem = self._viagem(remessa='0', venda='100')

        self.assertEqual(RetornoViagemService.previsto(viagem), [])


class ConferenciaTests(RetornoViagemBase):
    """O sistema calcula, a pessoa confere."""

    def _chave(self, viagem):
        saldo = SaldoCarga.objects.get(viagem=viagem, produto=self.produto)
        return (saldo.produto_id, saldo.lote_id)

    def test_registrar_tudo_devolve_o_previsto_ao_estoque(self):
        viagem = self._viagem(remessa='300')
        self._vender(viagem, '180')
        antes = self._saldo_estoque()

        resultado = RetornoViagemService.registrar_tudo(viagem, self.usuario)

        self.assertEqual(resultado['registrado'], Decimal('120.000'))
        self.assertEqual(self._saldo_estoque(), antes + Decimal('120'))
        self.assertEqual(
            RetornoViagemService.previsto(viagem)[0]['a_retornar'], ZERO,
        )

    def test_a_conferencia_aceita_menos_e_acusa_a_diferenca(self):
        """
        120 previstos e 118 contados são duas caixas que precisam de
        explicação, não um arredondamento.
        """
        viagem = self._viagem(remessa='300')
        self._vender(viagem, '180')

        resultado = RetornoViagemService.registrar(
            viagem, {self._chave(viagem): '118'}, self.usuario,
        )

        self.assertEqual(resultado['registrado'], Decimal('118'))
        self.assertEqual(len(resultado['divergencias']), 1)
        self.assertEqual(
            resultado['divergencias'][0]['diferenca'], Decimal('2.000'),
        )

    def test_a_diferenca_nao_vira_baixa_automatica(self):
        """
        Baixa é declaração de perda, com responsável — um sistema que a emite
        sozinho ensina a fábrica a não olhar.
        """
        viagem = self._viagem(remessa='300')
        self._vender(viagem, '180')
        RetornoViagemService.registrar(
            viagem, {self._chave(viagem): '118'}, self.usuario,
        )

        saldo = SaldoCarga.objects.get(viagem=viagem, produto=self.produto)
        self.assertEqual(saldo.quantidade_baixada, ZERO)
        # As duas continuam em poder da viagem, cobrando destino.
        self.assertEqual(saldo.quantidade_em_poder, Decimal('2.000'))

    def test_nao_se_retorna_mais_do_que_esta_em_poder(self):
        viagem = self._viagem(remessa='300')
        self._vender(viagem, '180')

        with self.assertRaises(DadosInvalidosError):
            RetornoViagemService.registrar(
                viagem, {self._chave(viagem): '200'}, self.usuario,
            )

    def test_sem_quantidade_informada_a_conferencia_recusa(self):
        viagem = self._viagem(remessa='300')

        with self.assertRaises(DadosInvalidosError):
            RetornoViagemService.registrar(viagem, {}, self.usuario)

    def test_registrar_tudo_sem_saldo_recusa(self):
        viagem = self._viagem(remessa='300')
        RetornoViagemService.registrar_tudo(viagem, self.usuario)

        with self.assertRaises(DadosInvalidosError):
            RetornoViagemService.registrar_tudo(viagem, self.usuario)

    def test_depois_do_retorno_a_viagem_pode_encerrar(self):
        viagem = self._viagem(remessa='300')
        self._vender(viagem, '180')
        self.assertTrue(RetornoViagemService.pode_encerrar(viagem))

        RetornoViagemService.registrar_tudo(viagem, self.usuario)

        self.assertEqual(RetornoViagemService.pode_encerrar(viagem), [])


class TelaTests(RetornoViagemBase):
    """A tela de conferência."""

    def _abrir(self, viagem):
        return self.client.get(
            reverse('logistica:viagem-retorno', args=[viagem.pk]),
        ).content.decode()

    def test_a_tela_mostra_a_conta(self):
        viagem = self._viagem(remessa='300')
        self._vender(viagem, '180')

        html = self._abrir(viagem)

        self.assertIn('Conferência de retorno', html)
        self.assertIn('Enviado sem comprador', html)
        self.assertIn('Deve retornar', html)
        self.assertIn('120', html)

    def test_o_campo_vem_preenchido_com_o_previsto(self):
        """Redigitar o que o sistema já calculou é chance de errar."""
        viagem = self._viagem(remessa='300')
        self._vender(viagem, '180')

        html = self._abrir(viagem)

        #  corta os zeros a' direita: 120,000 vira 120,
        # que e' o que a doca digita.
        self.assertIn('value="120"', html)

    def test_tudo_voltou_pela_tela(self):
        viagem = self._viagem(remessa='300')
        self._vender(viagem, '180')

        self.client.post(
            reverse('logistica:viagem-retorno', args=[viagem.pk]),
            {'acao': 'tudo'},
        )

        self.assertEqual(
            RetornoViagemService.previsto(viagem)[0]['a_retornar'], ZERO,
        )

    def test_conferencia_parcial_pela_tela(self):
        viagem = self._viagem(remessa='300')
        self._vender(viagem, '180')
        saldo = SaldoCarga.objects.get(viagem=viagem, produto=self.produto)

        resposta = self.client.post(
            reverse('logistica:viagem-retorno', args=[viagem.pk]),
            {
                'acao': 'conferir',
                f'retorno-{saldo.produto_id}-0': '118',
            },
            follow=True,
        )

        saldo.refresh_from_db()
        self.assertEqual(saldo.quantidade_retornada, Decimal('118.000'))
        self.assertIn('faltam', resposta.content.decode())

    def test_a_viagem_leva_ate_a_conferencia(self):
        viagem = self._viagem(remessa='300')

        html = self.client.get(
            reverse('logistica:viagem-detail', args=[viagem.pk]),
        ).content.decode()

        self.assertIn('Conferir retorno', html)

    def test_sem_saldo_a_viagem_nao_oferece_a_conferencia(self):
        """Tela vazia ensina a ignorar o botão."""
        viagem = self._viagem(remessa='0', venda='100')

        html = self.client.get(
            reverse('logistica:viagem-detail', args=[viagem.pk]),
        ).content.decode()

        self.assertNotIn('Conferir retorno', html)
