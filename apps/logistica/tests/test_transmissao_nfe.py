"""
A transmissão para a SEFAZ das notas que a viagem emite.

O CICLO QUE PARAVA NA PORTA. Remessa, venda na rua, bonificação e retorno
nasciam conferidas, numeradas — e ficavam `PENDENTE` para sempre. Uma nota
que o ERP considera emitida e a SEFAZ nunca viu é pior do que nota nenhuma:
o estoque baixou, o cliente levou a mercadoria, o número foi consumido, e não
existe documento amparando nada disso.

O QUE ESTES TESTES CERCAM:

  · AS QUATRO NATUREZAS TRANSMITEM pela mesma porta, cada uma levando o seu
    próprio payload;

  · VAI O QUE FOI CONFERIDO, e não o que mudou depois: entre emitir e
    transmitir a operação continua andando, e remontar mandaria à SEFAZ
    números diferentes dos que o ERP registrou na nota;

  · A AUTORIZAÇÃO É ASSÍNCRONA. Enquanto a resposta não volta a nota fica
    "enviando" — mostrar "autorizada" no otimismo faria alguém imprimir
    DANFE de nota que a SEFAZ ainda vai rejeitar;

  · REJEIÇÃO NÃO QUEIMA A NOTA: transmite-se de novo com o mesmo número,
    porque número reservado e não usado vira buraco que a SEFAZ cobra com
    inutilização;

  · SEM TOKEN A RECUSA É EXPLICADA, e não um erro de sistema.
"""
from decimal import Decimal

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente, ClienteFilial
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import StatusDocumentoFiscal
from apps.financeiro.models.formas_pagamento import (
    CondicaoPagamento, FormaPagamento,
)
from apps.financeiro.models.receber_pagar import ContaReceber
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao
from apps.fiscal.services.natureza_operacao_service import (
    NaturezaOperacaoService,
)
from apps.logistica.models import (
    EntregaBonificacao, ItemCarga, ItemVendaViagem, SaldoCarga, VendaViagem,
    Viagem,
)
from apps.logistica.services.bonificacao_nfe import BonificacaoNFeService
from apps.logistica.services.entrega_bonificacao import (
    EntregaBonificacaoService,
)
from apps.logistica.services.bonificacao_nfe import BonificacaoNFeService
from apps.logistica.services.transmissao_nfe import (
    TransmissaoNFeViagemService,
)
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


class FiscalBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Sefaz LTDA', nome_fantasia='Sefaz',
            cnpj='11345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='11345678000272',
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
            email='sefaz@rota.local', nome='Sefaz', password='x' * 12,
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




    def _carga_das_tres_naturezas(self):
        """Um caminhão, três operações: venda, remessa e bonificação."""
        return self._viagem([
            {'natureza': self.natureza_venda, 'cliente': self.cliente,
             'quantidade': '150'},
            {'natureza': self.natureza_remessa, 'quantidade': '200'},
            {'natureza': self.natureza_bonif, 'cliente': self.cliente,
             'quantidade': '10'},
        ])




class FocusFalsa:
    """
    A Focus, sem rede.

    IMITA A CONVERSA, e não o serviço: o `FocusNFeService` de verdade roda por
    cima disto, então o que os testes verificam é o caminho real — payload,
    status, log — e não uma simulação do nosso próprio código.
    """

    endpoint = 'nfe'

    def __init__(self, retorno=None, erro=None):
        self.retorno = retorno or {'status': 'processando_autorizacao'}
        self.erro = erro
        self.enviados = []
        self.consultas = []

    # ── O que o FocusNFeService chama ────────────────────────────────────
    @property
    def nfe(self):
        return self

    def autorizar(self, ref, payload):
        self.enviados.append((ref, payload))
        if self.erro is not None:
            raise self.erro
        return self.retorno

    def consultar(self, ref):
        self.consultas.append(ref)
        return self.retorno


class TransmissaoBase(FiscalBase):

    def setUp(self):
        super().setUp()
        self.filial.focusnfe_token = 'token-de-teste'
        self.filial.save(update_fields=['focusnfe_token'])
        self.focus = FocusFalsa()

    def _com_focus(self, focus=None):
        """Troca só o transporte: o serviço de emissão continua o real."""
        from apps.fiscal.services.focusnfe_service import FocusNFeService

        servico = FocusNFeService(client=focus or self.focus)
        return patch.object(
            TransmissaoNFeViagemService, '_servico', return_value=servico,
        )

    def _viagem_na_rua(self, quantidade='300'):
        viagem = self._viagem([
            {'natureza': self.natureza_remessa, 'quantidade': quantidade},
        ])
        viagem.status = Viagem.Status.EM_VENDAS
        viagem.save(update_fields=['status'])
        return viagem

    def _vender(self, viagem, quantidade='100'):
        return VendaViagemService.registrar(viagem, {
            'produto': self.produto, 'quantidade': quantidade,
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)


class TransmissaoTests(TransmissaoBase):
    """A nota chega na SEFAZ."""

    def test_a_remessa_vai_para_a_sefaz(self):
        viagem = self._viagem_na_rua()
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)

        with self._com_focus():
            TransmissaoNFeViagemService.transmitir(remessa, self.usuario)

        remessa.refresh_from_db()
        self.assertEqual(remessa.status, StatusDocumentoFiscal.PROCESSANDO)
        self.assertEqual(len(self.focus.enviados), 1)

    def test_vai_o_payload_conferido_na_emissao(self):
        """
        Entre emitir e transmitir a operação continua andando. Remontar agora
        mandaria à SEFAZ números diferentes dos que esta nota registrou.
        """
        viagem = self._viagem_na_rua()
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)
        self.assertTrue(remessa.payload_envio)

        with self._com_focus():
            TransmissaoNFeViagemService.transmitir(remessa, self.usuario)

        _, enviado = self.focus.enviados[0]
        self.assertEqual(enviado, remessa.payload_envio)
        self.assertEqual(enviado['items'][0]['quantidade_comercial'], 300.0)

    def test_as_quatro_naturezas_passam_pela_mesma_porta(self):
        viagem = self._viagem_na_rua()
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)
        venda = self._vender(viagem, '100')
        nota_venda = VendaForaNFeService.emitir(venda, self.usuario)
        bonificacao = VendaViagemService.registrar(viagem, {
            'tipo': VendaViagem.Tipo.BONIFICACAO,
            'motivo': VendaViagem.Motivo.BRINDE,
            'produto': self.produto, 'quantidade': '10',
            'valor_unitario': '10', 'cliente': self.cliente,
        }, usuario=self.usuario)
        nota_bonificacao = BonificacaoNFeService.emitir(bonificacao, self.usuario)
        ViagemService.registrar_retorno(
            viagem, self.produto, Decimal('190'), usuario=self.usuario,
        )
        nota_retorno = RetornoVendaForaService.emitir(viagem, self.usuario)

        with self._com_focus():
            for documento in (remessa, nota_venda, nota_bonificacao, nota_retorno):
                TransmissaoNFeViagemService.transmitir(documento, self.usuario)

        self.assertEqual(len(self.focus.enviados), 4)
        for documento in (remessa, nota_venda, nota_bonificacao, nota_retorno):
            documento.refresh_from_db()
            self.assertEqual(documento.status, StatusDocumentoFiscal.PROCESSANDO)

    def test_a_autorizacao_que_volta_na_hora_e_aplicada(self):
        """
        Quando a SEFAZ responde na hora, a nota já sai autorizada — o
        assíncrono é o caso comum, não o único.
        """
        viagem = self._viagem_na_rua()
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)
        focus = FocusFalsa(retorno={
            'status': 'autorizado',
            'chave_nfe': '8' * 44,
            'protocolo': '987654321',
        })

        with self._com_focus(focus):
            TransmissaoNFeViagemService.transmitir(remessa, self.usuario)

        remessa.refresh_from_db()
        self.assertEqual(remessa.status, StatusDocumentoFiscal.AUTORIZADA)
        self.assertEqual(remessa.chave, '8' * 44)

    def test_as_notas_da_viagem_sao_lidas_pela_origem(self):
        viagem = self._viagem_na_rua()
        RemessaVendaForaService.emitir(viagem, self.usuario)
        venda = self._vender(viagem, '100')
        VendaForaNFeService.emitir(venda, self.usuario)

        notas = TransmissaoNFeViagemService.da_viagem(viagem)

        self.assertEqual(len(notas), 2)
        self.assertEqual(
            {n.origem_tipo for n in notas},
            {'viagem_remessa', 'viagem_venda_fora'},
        )


class RecusaTests(TransmissaoBase):
    """Quando não dá para transmitir, a tela diz por quê."""

    def test_sem_token_a_recusa_manda_a_um_lugar(self):
        """"Falhou" manda a pessoa adivinhar."""
        self.filial.focusnfe_token = ''
        self.filial.save(update_fields=['focusnfe_token'])
        viagem = self._viagem_na_rua()
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)

        with self.assertRaises(DadosInvalidosError) as erro:
            TransmissaoNFeViagemService.transmitir(remessa, self.usuario)

        self.assertIn('token da Focus NFe', str(erro.exception))

    def test_nota_autorizada_nao_volta_para_a_fila(self):
        viagem = self._viagem_na_rua()
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)
        remessa.status = StatusDocumentoFiscal.AUTORIZADA
        remessa.save(update_fields=['status'])

        self.assertEqual(
            TransmissaoNFeViagemService.pode_transmitir(remessa),
            'Nota já autorizada pela SEFAZ.',
        )

    def test_nota_enviada_nao_e_reenviada_por_engano(self):
        """
        Reenviar o que já está na fila da SEFAZ é pedir duplicidade — e a
        resposta demora justamente quando alguém está com pressa.
        """
        viagem = self._viagem_na_rua()
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)
        remessa.status = StatusDocumentoFiscal.PROCESSANDO
        remessa.save(update_fields=['status'])

        self.assertIn(
            'aguardando', TransmissaoNFeViagemService.pode_transmitir(remessa),
        )

    def test_rejeitada_pode_ser_transmitida_de_novo_com_o_mesmo_numero(self):
        """
        Número reservado e não usado vira buraco na numeração, que a SEFAZ
        cobra depois com inutilização.
        """
        viagem = self._viagem_na_rua()
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)
        remessa.status = StatusDocumentoFiscal.REJEITADA
        remessa.mensagem_sefaz = '539 - Duplicidade de NF-e'
        remessa.save(update_fields=['status', 'mensagem_sefaz'])
        numero = remessa.numero

        self.assertEqual(TransmissaoNFeViagemService.pode_transmitir(remessa), '')
        with self._com_focus():
            TransmissaoNFeViagemService.transmitir(remessa, self.usuario)

        remessa.refresh_from_db()
        self.assertEqual(remessa.numero, numero)

    def test_a_recusa_da_sefaz_fica_gravada_na_nota(self):
        from apps.fiscal.integrations.focusnfe.exceptions import FocusNFeError

        viagem = self._viagem_na_rua()
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)
        erro = FocusNFeError('Rejeicao: destinatario sem inscricao')
        erro.response_json = {
            'status_sefaz': '210', 'mensagem_sefaz': 'Destinatario sem inscricao',
        }
        erro.status_code = 422

        with self._com_focus(FocusFalsa(erro=erro)):
            with self.assertRaises(FocusNFeError):
                TransmissaoNFeViagemService.transmitir(remessa, self.usuario)

        remessa.refresh_from_db()
        self.assertEqual(remessa.status, StatusDocumentoFiscal.REJEITADA)
        self.assertIn('Destinatario sem inscricao', remessa.mensagem_sefaz)


class TelaTests(TransmissaoBase):
    """O botão vive ao lado da nota."""

    def test_a_tela_da_viagem_oferece_transmitir(self):
        viagem = self._viagem_na_rua()
        RemessaVendaForaService.emitir(viagem, self.usuario)

        html = self.client.get(
            reverse('logistica:viagem-detail', args=[viagem.pk]),
        ).content.decode()

        self.assertIn('Transmitir à SEFAZ', html)

    def test_transmitir_pela_tela(self):
        viagem = self._viagem_na_rua()
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)

        with self._com_focus():
            self.client.post(reverse(
                'logistica:viagem-nfe-transmitir', args=[viagem.pk, remessa.pk],
            ))

        remessa.refresh_from_db()
        self.assertEqual(remessa.status, StatusDocumentoFiscal.PROCESSANDO)

    def test_enviada_a_tela_mostra_que_espera_a_sefaz(self):
        viagem = self._viagem_na_rua()
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)
        remessa.status = StatusDocumentoFiscal.PROCESSANDO
        remessa.save(update_fields=['status'])

        html = self.client.get(
            reverse('logistica:viagem-detail', args=[viagem.pk]),
        ).content.decode()

        self.assertIn('aguardando a SEFAZ', html)
        self.assertIn('Consultar SEFAZ', html)

    def test_consultar_pela_tela(self):
        viagem = self._viagem_na_rua()
        remessa = RemessaVendaForaService.emitir(viagem, self.usuario)
        remessa.status = StatusDocumentoFiscal.PROCESSANDO
        remessa.save(update_fields=['status'])
        focus = FocusFalsa(retorno={
            'status': 'autorizado',
            'chave_nfe': '5' * 44,
            'protocolo': '123456789',
        })

        with self._com_focus(focus):
            self.client.post(
                reverse(
                    'logistica:viagem-nfe-transmitir',
                    args=[viagem.pk, remessa.pk],
                ),
                {'acao': 'consultar'},
            )

        remessa.refresh_from_db()
        self.assertEqual(remessa.status, StatusDocumentoFiscal.AUTORIZADA)
        self.assertEqual(remessa.chave, '5' * 44)
