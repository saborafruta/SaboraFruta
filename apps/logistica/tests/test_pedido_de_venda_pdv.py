"""
Pedido de expedição a partir de uma venda do PDV com NF-e.

O QUE ESTES TESTES CERCAM:

  · SÓ ENTRA QUEM TEM ENTREGA MARCADA E NF-e AUTORIZADA. Venda de balcão
    (sem `delivery`) ou sem NF-e não precisa de expedição nem MDF-e;

  · A ESCOLHA É MANUAL — a lista mostra quem é elegível, o pedido nasce
    só quando alguém escolhe, nunca sozinho ao emitir a NF-e;

  · NÃO GERA COBRANÇA. A venda já foi paga no PDV; um título aqui cobraria
    o cliente duas vezes;

  · UMA VENDA NÃO VIRA DOIS PEDIDOS — depois de gerado, ela some da lista
    de elegíveis.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError
from apps.financeiro.constants.enums import StatusDocumentoFiscal, TipoDocumentoFiscal
from apps.financeiro.models.fiscal import DocumentoFiscal
from apps.logistica.models import PedidoExpedicao
from apps.logistica.services import pedido_de_venda_pdv as PedidoDeVendaPdvService
from apps.pdv.models import ItemVendaPDV, VendaPDV
from apps.produtos.models import Produto, ProdutoFilial, UnidadeMedida, UnidadeMedidaFilial


class PedidoDeVendaPdvBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Expedicao PDV LTDA', nome_fantasia='Expedicao PDV',
            cnpj='82345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='82345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='expedicao-pdv@rota.local', nome='Expedicao PDV',
            password='x' * 12, empresa=cls.empresa, filial=cls.filial, perfil=cls.perfil,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Cliente da Entrega',
            cpf_cnpj='12345678000190', ativo=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='UN', descricao='Unidade',
            tipo=UnidadeMedida.Tipo.UNIDADE,
        )
        UnidadeMedidaFilial.objects.create(unidade=cls.unidade, filial=cls.filial)

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

    def _produto(self, codigo='PROD-01'):
        produto = Produto.objects.create(
            filial=self.filial, unidade_medida=self.unidade, descricao='Produto entregue',
            codigo=codigo, ncm='20089900', preco_venda=Decimal('10.00'),
        )
        ProdutoFilial.objects.create(produto=produto, filial=self.filial)
        return produto

    def _venda(self, delivery=True, cliente=None, status='finalizada',
               endereco_entrega=None, **extra):
        venda = VendaPDV.objects.create(
            filial=self.filial, numero_venda=self._numero_venda(), status=status,
            delivery=delivery, cliente=cliente if cliente is not None else self.cliente,
            endereco_entrega=endereco_entrega or {
                'rua': 'Rua das Entregas', 'numero': '100', 'bairro': 'Centro',
                'cidade': 'Natal', 'uf': 'RN', 'cep': '59000000',
            },
            valor_total=Decimal('100.00'), usuario=self.usuario, data_venda=timezone.now(),
            **extra,
        )
        produto = self._produto(codigo=f'PROD-{venda.pk}')
        ItemVendaPDV.objects.create(
            venda_pdv=venda, produto=produto, numero_item=1,
            quantidade=Decimal('2'), unidade_medida='UN',
            valor_unitario=Decimal('50.00'), valor_total=Decimal('100.00'),
        )
        return venda

    _contador_venda = 0

    def _numero_venda(self):
        type(self)._contador_venda += 1
        return type(self)._contador_venda

    def _nfe_autorizada(self, venda, status=StatusDocumentoFiscal.AUTORIZADA):
        return DocumentoFiscal.objects.create(
            filial=self.filial, tipo_documento=TipoDocumentoFiscal.NFE,
            origem_tipo='venda_pdv', origem_id=venda.pk,
            numero=venda.pk, serie=1, emitente_cnpj='82345678000272',
            destinatario_snapshot={}, valor_total=venda.valor_total,
            status=status, data_emissao=timezone.now(), usuario=self.usuario,
        )


class VendasElegiveisTests(PedidoDeVendaPdvBase):

    def test_venda_com_entrega_e_nfe_autorizada_e_elegivel(self):
        venda = self._venda()
        self._nfe_autorizada(venda)

        elegiveis = PedidoDeVendaPdvService.vendas_pdv_elegiveis(self.filial)

        self.assertIn(venda, list(elegiveis))

    def test_venda_de_balcao_sem_entrega_fica_de_fora(self):
        venda = self._venda(delivery=False)
        self._nfe_autorizada(venda)

        elegiveis = PedidoDeVendaPdvService.vendas_pdv_elegiveis(self.filial)

        self.assertNotIn(venda, list(elegiveis))

    def test_venda_sem_nfe_fica_de_fora(self):
        venda = self._venda()

        elegiveis = PedidoDeVendaPdvService.vendas_pdv_elegiveis(self.filial)

        self.assertNotIn(venda, list(elegiveis))

    def test_venda_com_nfe_cancelada_fica_de_fora(self):
        venda = self._venda()
        self._nfe_autorizada(venda, status=StatusDocumentoFiscal.CANCELADA)

        elegiveis = PedidoDeVendaPdvService.vendas_pdv_elegiveis(self.filial)

        self.assertNotIn(venda, list(elegiveis))

    def test_venda_que_ja_tem_pedido_some_da_lista(self):
        venda = self._venda()
        self._nfe_autorizada(venda)
        PedidoDeVendaPdvService.gerar_pedido_expedicao(venda, self.usuario)

        elegiveis = PedidoDeVendaPdvService.vendas_pdv_elegiveis(self.filial)

        self.assertNotIn(venda, list(elegiveis))


class GerarPedidoTests(PedidoDeVendaPdvBase):

    def test_gera_pedido_com_cliente_endereco_e_itens(self):
        venda = self._venda()
        self._nfe_autorizada(venda)

        pedido = PedidoDeVendaPdvService.gerar_pedido_expedicao(venda, self.usuario)

        self.assertEqual(pedido.venda_pdv_id, venda.pk)
        self.assertEqual(pedido.cliente_id, self.cliente.pk)
        self.assertEqual(pedido.endereco_entrega['endereco'], 'Rua das Entregas')
        self.assertEqual(pedido.endereco_entrega['cidade'], 'Natal')
        self.assertEqual(pedido.itens.count(), 1)
        item = pedido.itens.get()
        self.assertEqual(item.quantidade, Decimal('2'))
        self.assertEqual(item.valor_unitario, Decimal('50.00'))
        self.assertEqual(pedido.valor_total, Decimal('100.00'))

    def test_nao_gera_cobranca_nenhuma(self):
        venda = self._venda()
        self._nfe_autorizada(venda)

        pedido = PedidoDeVendaPdvService.gerar_pedido_expedicao(venda, self.usuario)

        self.assertIsNone(pedido.forma_pagamento_id)
        self.assertIsNone(pedido.condicao_pagamento_id)

    def test_recusa_venda_sem_entrega(self):
        venda = self._venda(delivery=False)
        self._nfe_autorizada(venda)

        with self.assertRaises(DadosInvalidosError):
            PedidoDeVendaPdvService.gerar_pedido_expedicao(venda, self.usuario)

    def test_recusa_venda_sem_nfe_autorizada(self):
        venda = self._venda()

        with self.assertRaises(DadosInvalidosError):
            PedidoDeVendaPdvService.gerar_pedido_expedicao(venda, self.usuario)

    def test_recusa_gerar_duas_vezes_da_mesma_venda(self):
        venda = self._venda()
        self._nfe_autorizada(venda)
        PedidoDeVendaPdvService.gerar_pedido_expedicao(venda, self.usuario)

        with self.assertRaises(DadosInvalidosError):
            PedidoDeVendaPdvService.gerar_pedido_expedicao(venda, self.usuario)

        self.assertEqual(PedidoExpedicao.objects.filter(venda_pdv=venda).count(), 1)


class TelaTests(PedidoDeVendaPdvBase):

    def test_a_tela_lista_as_elegiveis(self):
        venda = self._venda()
        self._nfe_autorizada(venda)

        resposta = self.client.get(reverse('logistica:pedido-expedicao-de-vendas-pdv'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, f'#{venda.numero_venda:06d}')

    def test_gerar_pelo_post_cria_o_pedido_e_redireciona_ao_detalhe(self):
        venda = self._venda()
        self._nfe_autorizada(venda)

        resposta = self.client.post(
            reverse('logistica:pedido-expedicao-de-vendas-pdv'),
            {'venda_id': [str(venda.pk)]},
        )

        pedido = PedidoExpedicao.objects.get(venda_pdv=venda)
        self.assertRedirects(
            resposta, reverse('logistica:pedido-expedicao-detail', args=[pedido.pk]),
        )

    def test_sem_selecionar_nada_nao_cria_pedido(self):
        resposta = self.client.post(
            reverse('logistica:pedido-expedicao-de-vendas-pdv'), {},
        )

        self.assertEqual(PedidoExpedicao.objects.count(), 0)
        self.assertRedirects(
            resposta, reverse('logistica:pedido-expedicao-de-vendas-pdv'),
        )
