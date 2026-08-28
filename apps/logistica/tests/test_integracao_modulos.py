"""
As três cadeias do módulo de viagem, ligadas de ponta a ponta.

    Venda      → item → viagem → carga → NF-e → MDF-e
    Remessa    → item → viagem → carga → venda / retorno
    Bonificação→ item → viagem → carga → NF-e → entrega / retorno

POR QUE ESTES TESTES EXISTEM, JÁ QUE CADA PEDAÇO TEM O SEU

Cada serviço é testado sozinho, e sozinho cada um passa. O que quebra na
prática é o elo: alguém troca o `related_name`, muda a origem do documento
fiscal, filtra por outro status — e a peça continua verde enquanto a cadeia
se rompe em silêncio. Estes testes seguram os elos, e não as peças.

O QUE CADA CADEIA PRECISA CARREGAR:

  · CLIENTE, PRODUTO, LOTE e FILIAL viajam junto em cada elo — sem eles a
    cadeia responde "quanto" mas não responde "de quem" nem "de onde";

  · O PEDIDO DE VENDA continua ligado à carga, senão a mesma mercadoria sai
    duas vezes: uma no faturamento, outra no caminhão;

  · A NF-e é encontrada pela ORIGEM, e o MDF-e consolida as notas da viagem
    sem mudar a natureza de nenhuma delas;

  · A VENDA A PRAZO VIRA COBRANÇA. Venda sem título é venda que a empresa
    esquece de cobrar — e ninguém erra nada visível no caminho.
"""
from decimal import Decimal

from django.test import TestCase

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import StatusContaReceber
from apps.financeiro.models.formas_pagamento import (
    CondicaoPagamento, FormaPagamento,
)
from apps.financeiro.models.receber_pagar import ContaReceber
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.logistica.models import (
    EntregaBonificacao, ItemCarga, ItemVendaViagem, SaldoCarga, VendaViagem,
    Viagem,
)
from apps.logistica.services.bonificacao_nfe import BonificacaoNFeService
from apps.logistica.services.entrega_bonificacao import (
    EntregaBonificacaoService,
)
from apps.logistica.services.financeiro_viagem import FinanceiroViagemService
from apps.logistica.services.mdfe_viagem import MDFeViagemService
from apps.logistica.services.remessa_nfe import RemessaVendaForaService
from apps.logistica.services.retorno_nfe import RetornoVendaForaService
from apps.logistica.services.venda_fora_nfe import VendaForaNFeService
from apps.logistica.services.venda_viagem import VendaViagemService
from apps.logistica.services.viagem import ViagemService
from apps.logistica.services.vinculo_remessa import VinculoRemessaService
from apps.produtos.models import Produto, ProdutoFilial
from apps.produtos.models.unidade import UnidadeMedida, UnidadeMedidaFilial

ZERO = Decimal('0')
T = VendaViagem.Tipo


class IntegracaoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Cadeia LTDA', nome_fantasia='Cadeia',
            cnpj='61345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='61345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
            endereco='Av. Principal', numero='100', bairro='Centro',
            cep='59000000', inscricao_estadual='123456789',
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
            email='cadeia@rota.local', nome='Cadeia', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado da Esquina',
            cpf_cnpj='12345678901', uf='RN', cidade='Natal',
        )
        ClienteFilial.objects.create(cliente=cls.cliente, filial=cls.filial)

        for codigo, descricao, especie, cfop in (
            ('venda', 'Venda', NaturezaOperacao.Especie.VENDA, '5102'),
            ('remessa', 'Remessa', NaturezaOperacao.Especie.REMESSA_VENDA_FORA, '5904'),
            ('vendafora', 'Venda fora', NaturezaOperacao.Especie.VENDA_FORA, '5103'),
            ('retorno', 'Retorno', NaturezaOperacao.Especie.RETORNO_VENDA_FORA, '1904'),
            ('bonif', 'Bonificação', NaturezaOperacao.Especie.BONIFICACAO, '5910'),
        ):
            natureza = NaturezaOperacao.objects.create(
                filial=cls.filial, codigo=codigo, descricao=descricao,
                especie=especie,
                exige_destinatario=especie in (
                    NaturezaOperacao.Especie.VENDA,
                    NaturezaOperacao.Especie.VENDA_FORA,
                    NaturezaOperacao.Especie.BONIFICACAO,
                ),
            )
            RegraNaturezaOperacao.objects.create(natureza=natureza, cfop=cfop)
            setattr(cls, f'natureza_{codigo}', natureza)

        # A prazo gera título; dinheiro na entrega, não.
        cls.a_prazo = FormaPagamento.objects.create(
            empresa=cls.empresa, descricao='Boleto 30 dias', tipo='boleto',
            gera_parcelas=True, prazo_liquidacao_dias=30,
        )
        cls.a_vista = FormaPagamento.objects.create(
            empresa=cls.empresa, descricao='Dinheiro', tipo='dinheiro',
            gera_parcelas=False,
        )
        cls.condicao = CondicaoPagamento.objects.create(
            empresa=cls.empresa, descricao='3x 30/60/90',
            numero_parcelas=3, intervalo_dias=30, dias_primeira_parcela=30,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.produto = self._produto('P1', '5000')
        self.lote = LoteProduto.objects.create(
            filial=self.filial, produto=self.produto, numero_lote='L-1',
            quantidade_inicial=Decimal('1000'), quantidade_atual=Decimal('1000'),
        )

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

    def _viagem(self, itens):
        viagem = Viagem.objects.create(
            filial=self.filial, numero=Viagem.objects.count() + 1,
            motorista_nome='Seu Zé', veiculo_placa='ABC1D23',
            vendedor=self.usuario, responsavel=self.usuario,
        )
        for dados in itens:
            ViagemService.adicionar_item(viagem, {
                'produto': self.produto, 'valor_unitario': '10', **dados,
            })
        ViagemService.fechar_carga(viagem, usuario=self.usuario)
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])
        return viagem


class CadeiaDaVendaTests(IntegracaoBase):
    """Venda → item → viagem → carga → NF-e → MDF-e."""

    def test_a_carga_da_venda_carrega_cliente_produto_e_lote(self):
        viagem = self._viagem([{
            'natureza': self.natureza_venda, 'cliente': self.cliente,
            'lote': self.lote, 'quantidade': '150',
        }])

        item = ItemCarga.objects.get(viagem=viagem)

        self.assertEqual(item.cliente, self.cliente)
        self.assertEqual(item.produto, self.produto)
        self.assertEqual(item.lote, self.lote)
        self.assertEqual(item.viagem.filial, self.filial)

    def test_a_venda_ja_faturada_baixa_o_estoque_uma_vez_so(self):
        """
        O elo com Faturamento: a mesma caixa não pode sair no faturamento e
        de novo no caminhão.
        """
        viagem = self._viagem([{
            'natureza': self.natureza_venda, 'cliente': self.cliente,
            'quantidade': '150',
        }])

        saidas = MovimentacaoEstoque.objects.filter(
            documento_tipo='viagem', documento_id=viagem.pk,
            produto=self.produto,
        )

        self.assertEqual(saidas.count(), 1)
        self.assertEqual(saidas.first().quantidade, Decimal('150'))

    def test_a_nfe_da_venda_na_rua_aponta_para_a_venda(self):
        viagem = self._viagem([{
            'natureza': self.natureza_remessa, 'quantidade': '300',
        }])
        venda = VendaViagemService.registrar(viagem, {
            'produto': self.produto, 'quantidade': '180',
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)

        nota = VendaForaNFeService.emitir(venda, self.usuario)

        venda.refresh_from_db()
        self.assertEqual(venda.documento_fiscal, nota)
        self.assertEqual(nota.origem_id, venda.pk)
        self.assertEqual(nota.filial, self.filial)

    def test_o_mdfe_consolida_as_notas_da_viagem(self):
        viagem = self._viagem([{
            'natureza': self.natureza_remessa, 'quantidade': '300',
        }])
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)

        documentos = MDFeViagemService.documentos(viagem)

        self.assertIn(remessa.pk, [linha['documento'].pk for linha in documentos])


class CadeiaDaRemessaTests(IntegracaoBase):
    """Remessa → item → viagem → carga → venda / retorno."""

    def test_o_item_vendido_guarda_a_remessa_que_o_amparou(self):
        viagem = self._viagem([{
            'natureza': self.natureza_remessa, 'quantidade': '300',
        }])
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)
        venda = VendaViagemService.registrar(viagem, {
            'produto': self.produto, 'quantidade': '180',
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)

        item = ItemVendaViagem.objects.get(venda=venda)

        self.assertEqual(item.remessa, remessa)

    def test_o_vinculo_liga_remessa_viagem_produto_e_venda(self):
        viagem = self._viagem([{
            'natureza': self.natureza_remessa, 'quantidade': '300',
        }])
        RemessaVendaForaService.emitir(viagem, self.usuario)
        VendaViagemService.registrar(viagem, {
            'produto': self.produto, 'quantidade': '180',
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)

        linhas = VinculoRemessaService.linhas(viagem)

        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]['produto'], self.produto)
        self.assertEqual(linhas[0]['vendida'], Decimal('180'))
        self.assertEqual(linhas[0]['saldo'], Decimal('120'))

    def test_o_retorno_volta_ao_estoque_e_a_nota_referencia_a_remessa(self):
        viagem = self._viagem([{
            'natureza': self.natureza_remessa, 'quantidade': '300',
        }])
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)
        remessa.chave = '4' * 44
        remessa.save(update_fields=['chave'])
        VendaViagemService.registrar(viagem, {
            'produto': self.produto, 'quantidade': '180',
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)
        ViagemService.registrar_retorno(
            viagem, self.produto, Decimal('120'), usuario=self.usuario,
        )

        vinculo = RetornoVendaForaService.vinculo(viagem)

        self.assertEqual(vinculo['chave'], '4' * 44)
        self.assertEqual(vinculo['quantidade'], Decimal('120'))
        self.assertTrue(
            MovimentacaoEstoque.objects.filter(
                documento_tipo='viagem_retorno', documento_id=viagem.pk,
            ).exists()
        )


class CadeiaDaBonificacaoTests(IntegracaoBase):
    """Bonificação → item → viagem → carga → NF-e → entrega / retorno."""

    def test_a_bonificacao_da_carga_tem_acompanhamento_de_entrega(self):
        viagem = self._viagem([{
            'natureza': self.natureza_bonif, 'cliente': self.cliente,
            'quantidade': '20',
        }])
        item = ItemCarga.objects.get(viagem=viagem)

        acompanhamento = EntregaBonificacaoService.para_item(item)

        self.assertIsNotNone(acompanhamento)
        self.assertEqual(acompanhamento.item_carga, item)
        self.assertEqual(
            acompanhamento.status, EntregaBonificacao.Status.PENDENTE,
        )

    def test_a_bonificacao_da_rua_gera_nota_propria_e_nao_venda(self):
        viagem = self._viagem([{
            'natureza': self.natureza_remessa, 'quantidade': '300',
        }])
        entrega = VendaViagemService.registrar(viagem, {
            'tipo': T.BONIFICACAO, 'motivo': VendaViagem.Motivo.BRINDE,
            'produto': self.produto, 'quantidade': '10',
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)

        nota = BonificacaoNFeService.emitir(entrega, self.usuario)

        self.assertEqual(nota.origem_id, entrega.pk)
        saldo = SaldoCarga.objects.get(viagem=viagem, produto=self.produto)
        self.assertEqual(saldo.quantidade_bonificada, Decimal('10'))
        self.assertEqual(saldo.quantidade_vendida, ZERO)

    def test_a_bonificacao_nao_gera_cobranca(self):
        """Ninguém paga por uma cortesia."""
        viagem = self._viagem([{
            'natureza': self.natureza_remessa, 'quantidade': '300',
        }])
        entrega = VendaViagemService.registrar(viagem, {
            'tipo': T.BONIFICACAO, 'motivo': VendaViagem.Motivo.BRINDE,
            'produto': self.produto, 'quantidade': '10',
            'valor_unitario': '10', 'cliente': self.cliente,
            'forma_pagamento': self.a_prazo, 'condicao_pagamento': self.condicao,
        }, usuario=self.usuario)

        self.assertEqual(FinanceiroViagemService.titulos(entrega).count(), 0)


class CadeiaDoFinanceiroTests(IntegracaoBase):
    """A venda a prazo vira cobrança."""

    def _vender(self, viagem, forma=None, condicao=None, quantidade='180'):
        return VendaViagemService.registrar(viagem, {
            'produto': self.produto, 'quantidade': quantidade,
            'valor_unitario': '10', 'cliente': self.cliente,
            'forma_pagamento': forma, 'condicao_pagamento': condicao,
        }, usuario=self.usuario)

    def _na_rua(self):
        return self._viagem([{
            'natureza': self.natureza_remessa, 'quantidade': '300',
        }])

    def test_venda_a_prazo_abre_contas_a_receber(self):
        venda = self._vender(self._na_rua(), self.a_prazo, self.condicao)

        titulos = list(FinanceiroViagemService.titulos(venda))

        self.assertEqual(len(titulos), 3)
        self.assertEqual(
            sum(t.valor_final for t in titulos), Decimal('1800.00'),
        )
        self.assertEqual(titulos[0].cliente, self.cliente)
        self.assertEqual(titulos[0].filial, self.filial)
        self.assertEqual(titulos[0].documento_tipo, 'venda_viagem')
        self.assertEqual(titulos[0].status, StatusContaReceber.ABERTO)

    def test_as_parcelas_seguem_a_condicao_cadastrada(self):
        """
        A CONDIÇÃO É CADASTRO, e não código: mudar o parcelamento não pode
        exigir alterar o módulo de viagem.
        """
        self.condicao.numero_parcelas = 2
        self.condicao.intervalo_dias = 15
        self.condicao.save(update_fields=['numero_parcelas', 'intervalo_dias'])

        venda = self._vender(self._na_rua(), self.a_prazo, self.condicao)

        titulos = list(FinanceiroViagemService.titulos(venda))
        self.assertEqual(len(titulos), 2)
        self.assertEqual(
            (titulos[1].data_vencimento - titulos[0].data_vencimento).days, 15,
        )

    def test_o_centavo_que_sobra_vai_para_a_primeira_parcela(self):
        """
        Três parcelas de R$ 33,33 somam R$ 99,99 — e o centavo que falta faz
        o cliente dever um centavo para sempre.
        """
        venda = self._vender(
            self._na_rua(), self.a_prazo, self.condicao, quantidade='10',
        )
        # 10 × R$ 10,00 = R$ 100,00 em 3 parcelas.
        titulos = list(FinanceiroViagemService.titulos(venda))

        self.assertEqual([t.valor_final for t in titulos], [
            Decimal('33.34'), Decimal('33.33'), Decimal('33.33'),
        ])
        self.assertEqual(sum(t.valor_final for t in titulos), Decimal('100.00'))

    def test_dinheiro_na_entrega_nao_abre_titulo(self):
        """
        Conta a receber já quitada encheria o contas a receber de linhas que
        ninguém precisa cobrar.
        """
        venda = self._vender(self._na_rua(), self.a_vista)

        self.assertEqual(FinanceiroViagemService.titulos(venda).count(), 0)
        self.assertTrue(FinanceiroViagemService.resumo(venda)['a_vista'])

    def test_mais_um_item_ajusta_a_cobranca(self):
        viagem = self._na_rua()
        venda = self._vender(viagem, self.a_prazo, self.condicao, '100')

        VendaViagemService.adicionar_item(venda, {
            'produto': self.produto, 'quantidade': '50', 'valor_unitario': '10',
        })

        venda.refresh_from_db()
        titulos = list(FinanceiroViagemService.titulos(venda))
        self.assertEqual(sum(t.valor_final for t in titulos), Decimal('1500.00'))

    def test_cancelar_a_venda_cancela_a_cobranca(self):
        venda = self._vender(self._na_rua(), self.a_prazo, self.condicao)

        VendaViagemService.cancelar(venda, motivo='cliente desistiu')

        self.assertEqual(
            set(FinanceiroViagemService.titulos(venda).values_list('status', flat=True)),
            {StatusContaReceber.CANCELADO},
        )

    def test_venda_com_dinheiro_recebido_nao_cancela_por_cima(self):
        """
        Cancelar por cima de um título pago apagaria a cobrança e deixaria o
        recebimento órfão.
        """
        venda = self._vender(self._na_rua(), self.a_prazo, self.condicao)
        titulo = FinanceiroViagemService.titulos(venda).first()
        titulo.status = StatusContaReceber.PAGO_PARCIAL
        titulo.valor_pago = Decimal('100.00')
        titulo.save(update_fields=['status', 'valor_pago'])

        with self.assertRaises(DadosInvalidosError) as erro:
            VendaViagemService.cancelar(venda)

        self.assertIn('Estorne o pagamento', str(erro.exception))
        venda.refresh_from_db()
        self.assertEqual(venda.status, VendaViagem.Status.REGISTRADA)

    def test_registrar_duas_vezes_nao_duplica_cobranca(self):
        venda = self._vender(self._na_rua(), self.a_prazo, self.condicao)

        FinanceiroViagemService.gerar_titulos(venda, usuario=self.usuario)

        self.assertEqual(ContaReceber.objects.count(), 3)
