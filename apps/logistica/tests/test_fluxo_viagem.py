"""
A viagem inteira em uma linha: onde ela está e o que falta.

    VIAGEM → CARGA → MDF-e → TRANSPORTE → (vendas, bonificações, saldos)
           → RETORNO → NF-e DE RETORNO → CONFERÊNCIA → CONCILIAÇÃO
           → ENCERRAMENTO

O QUE ESTES TESTES CERCAM:

  · A ORDEM É A DA OPERAÇÃO, e não a do menu: quem abre uma viagem precisa
    saber qual é o PRÓXIMO passo, e não descobri-lo clicando;

  · UMA SÓ ETAPA EM DESTAQUE. Existe uma fila, não sete cobranças
    simultâneas;

  · ETAPA QUE NÃO SE APLICA NÃO É PENDENTE. Viagem sem remessa não vende na
    rua nem retorna — marcá-las de pendente ensina a ignorar o pendente;

  · O FLUXO NÃO DECIDE NADA. Ele lê o estado de cada dono de etapa; quem
    recusa encerrar continua sendo o `ViagemService`.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

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
from apps.logistica.services.fluxo_viagem import FluxoViagemService
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


class FluxoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Fluxo LTDA', nome_fantasia='Fluxo',
            cnpj='71345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='71345678000272',
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
            email='fluxo@rota.local', nome='Fluxo', password='x' * 12,
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




    def _etapas(self, viagem) -> dict:
        return {e['chave']: e for e in FluxoViagemService.etapas(viagem)}


class OrdemTests(FluxoBase):
    """A arquitetura, na ordem em que ela acontece."""

    def test_as_etapas_seguem_a_ordem_da_operacao(self):
        viagem = self._viagem([{
            'natureza': self.natureza_remessa, 'quantidade': '300',
        }])

        chaves = [e['chave'] for e in FluxoViagemService.etapas(viagem)]

        self.assertEqual(chaves, [
            'carga', 'mdfe', 'transporte', 'durante', 'retorno',
            'nfe_retorno', 'conferencia', 'conciliacao', 'encerramento',
        ])

    def test_a_carga_mostra_as_linhas_da_composicao(self):
        viagem = self._viagem([
            {'natureza': self.natureza_venda, 'cliente': self.cliente,
             'quantidade': '150'},
            {'natureza': self.natureza_remessa, 'quantidade': '200'},
            {'natureza': self.natureza_bonif, 'cliente': self.cliente,
             'quantidade': '10'},
        ])

        carga = self._etapas(viagem)['carga']

        self.assertEqual(
            [(l['rotulo'], l['quantidade']) for l in carga['linhas']],
            [
                ('Vendas já realizadas', Decimal('150')),
                ('Remessa para venda fora', Decimal('200')),
                ('Bonificações', Decimal('10')),
            ],
        )

    def test_uma_so_etapa_fica_em_destaque(self):
        """Existe uma fila, e não sete cobranças simultâneas."""
        viagem = self._viagem([{
            'natureza': self.natureza_remessa, 'quantidade': '300',
        }])

        etapas = FluxoViagemService.etapas(viagem)

        self.assertEqual(len([e for e in etapas if e['estado'] == 'atual']), 1)

    def test_a_etapa_atual_e_a_primeira_nao_resolvida(self):
        """Carga fechada e caminhão na rua: falta o MDF-e."""
        viagem = self._viagem([{
            'natureza': self.natureza_remessa, 'quantidade': '300',
        }])

        self.assertEqual(self._etapas(viagem)['mdfe']['estado'], 'atual')

    def test_etapa_que_nao_se_aplica_nao_e_pendencia(self):
        """
        Viagem que só leva venda já faturada não vende na rua nem retorna —
        marcá-las de pendente ensinaria a ignorar o pendente.
        """
        viagem = self._viagem([{
            'natureza': self.natureza_venda, 'cliente': self.cliente,
            'quantidade': '150',
        }])

        etapas = self._etapas(viagem)

        self.assertEqual(etapas['durante']['estado'], 'dispensada')
        self.assertEqual(etapas['retorno']['estado'], 'dispensada')
        self.assertEqual(etapas['nfe_retorno']['estado'], 'dispensada')

    def test_o_fluxo_nao_grava_nada(self):
        """Ler o fluxo não pode mexer no que ele descreve."""
        viagem = self._viagem([{
            'natureza': self.natureza_remessa, 'quantidade': '300',
        }])
        antes = (
            MovimentacaoEstoque.objects.count(),
            SaldoCarga.objects.count(),
            ItemCarga.objects.count(),
        )

        FluxoViagemService.etapas(viagem)

        self.assertEqual(antes, (
            MovimentacaoEstoque.objects.count(),
            SaldoCarga.objects.count(),
            ItemCarga.objects.count(),
        ))


class CicloCompletoTests(FluxoBase):
    """A viagem inteira, do fechamento da carga ao encerramento."""

    def test_a_viagem_percorre_todas_as_etapas(self):
        viagem = self._viagem([{
            'natureza': self.natureza_remessa, 'quantidade': '300',
        }])

        RemessaVendaForaService.emitir(viagem, self.usuario)
        self.assertEqual(self._etapas(viagem)['mdfe']['estado'], 'atual')

        VendaViagemService.registrar(viagem, {
            'produto': self.produto, 'quantidade': '180',
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)
        VendaViagemService.registrar(viagem, {
            'tipo': T.BONIFICACAO, 'motivo': VendaViagem.Motivo.BRINDE,
            'produto': self.produto, 'quantidade': '10',
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)

        durante = self._etapas(viagem)['durante']
        self.assertIn('180', durante['detalhe'])
        self.assertIn('110', durante['detalhe'])

        ViagemService.registrar_retorno(
            viagem, self.produto, Decimal('110'), usuario=self.usuario,
        )
        etapas = self._etapas(viagem)
        self.assertEqual(etapas['retorno']['estado'], 'concluida')
        self.assertEqual(etapas['conferencia']['estado'], 'concluida')
        self.assertEqual(etapas['conciliacao']['estado'], 'concluida')

        RetornoVendaForaService.emitir(viagem, self.usuario)
        self.assertEqual(
            self._etapas(viagem)['nfe_retorno']['estado'], 'concluida',
        )

        ViagemService.encerrar(viagem)

        etapas = self._etapas(viagem)
        self.assertEqual(etapas['encerramento']['estado'], 'concluida')
        # VIAGEM ENCERRADA NAO TEM "AGORA": nada mais vem a seguir, e o que
        # ficou por fazer -- aqui, o MDF-e que ninguem emitiu -- continua a'
        # vista como pendente, que e' o registro de que a viagem saiu assim.
        self.assertIsNone(
            FluxoViagemService.resumo(list(etapas.values()))['atual']
        )
        self.assertEqual(etapas['mdfe']['estado'], 'pendente')

    def test_com_saldo_na_rua_o_encerramento_continua_pendente(self):
        viagem = self._viagem([{
            'natureza': self.natureza_remessa, 'quantidade': '300',
        }])
        VendaViagemService.registrar(viagem, {
            'produto': self.produto, 'quantidade': '180',
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)

        etapas = self._etapas(viagem)

        self.assertEqual(etapas['conferencia']['estado'], 'pendente')
        self.assertEqual(etapas['encerramento']['estado'], 'pendente')
        self.assertIn('120', etapas['conferencia']['detalhe'])


class TelaTests(FluxoBase):
    """A trilha aparece no detalhe da viagem."""

    def test_o_detalhe_mostra_o_fluxo_e_a_etapa_de_agora(self):
        viagem = self._viagem([{
            'natureza': self.natureza_remessa, 'quantidade': '300',
        }])

        html = self.client.get(
            reverse('logistica:viagem-detail', args=[viagem.pk]),
        ).content.decode()

        self.assertIn('Fluxo da viagem', html)
        for titulo in ('Carga', 'MDF-e', 'Transporte', 'Retorno',
                       'Encerramento'):
            self.assertIn(titulo, html)
        self.assertIn('agora', html)
