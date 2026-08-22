"""
Emissão da ordem de produção pelo botão do pedido.

O usuário reportou 502 ao clicar em "Gerar ordem de produção". O log do
deploy não trazia traceback nenhum dessa rota — só o erro conhecido do IBPT
— e o horário batia com a janela de reinício do container. Estes testes
existem para separar as duas hipóteses de vez: se o caminho de código
funciona, o 502 foi infraestrutura, e não a view.

Cobrem também as RECUSAS, que são o comportamento esperado num pedido
incompleto: elas têm de virar mensagem na tela, nunca erro 500.
"""
from decimal import Decimal

from django.test import TestCase

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.core.services.exceptions import DomainError
from apps.moda.models import ItemPedidoProducao, OrdemProducao, PedidoProducao
from apps.moda.services import OrdemProducaoService


class GerarOrdemTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao OP LTDA', nome_fantasia='OP',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao OP LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Diego Macedo', cpf_cnpj='12345678901',
        )

    def _pedido(self, **campos):
        return PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=12, **campos,
        )

    def _item(self, pedido, quantidade=20):
        return ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Camisa de jogo em FDRY Tech',
            quantidade=quantidade, valor_unitario=Decimal('50.00'),
        )

    # ── O caminho recusa, mas recusa DIREITO ─────────────────────────────

    def test_pedido_sem_produto_recusa_com_DomainError(self):
        """
        Recusa é `DomainError`, que a view transforma em mensagem na tela.

        É o ponto que separa "o sistema disse não" de "o sistema quebrou":
        qualquer outra exceção aqui viraria 500 para quem clicou.
        """
        with self.assertRaises(DomainError):
            OrdemProducaoService.gerar_do_pedido(self._pedido())

    def test_pedido_cancelado_recusa_com_DomainError(self):
        pedido = self._pedido(status=PedidoProducao.Status.CANCELADO)
        self._item(pedido)

        with self.assertRaises(DomainError):
            OrdemProducaoService.gerar_do_pedido(pedido)

    def test_pedido_incompleto_recusa_com_DomainError_e_nao_com_500(self):
        """
        É o caso do usuário: o pedido dele acusa "ainda falta da ficha —
        Corte, Sublimação, Costura, Entrega". A validação tem de barrar
        com DomainError, e não estourar.
        """
        pedido = self._pedido()
        self._item(pedido)

        with self.assertRaises(DomainError):
            OrdemProducaoService.gerar_do_pedido(pedido)

        self.assertEqual(OrdemProducao.objects.count(), 0)

    # ── A view não devolve 500 em nenhum desses casos ────────────────────

    def test_a_view_devolve_redirect_e_nao_erro_quando_o_servico_recusa(self):
        """
        O 502 relatado não vem daqui: mesmo recusando, a view redireciona.

        Chamado sem passar pela URL para não precisar de login e perfil —
        o que se testa é o tratamento da recusa, não a permissão.
        """
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from apps.moda.views_ordem import OrdemGerarView

        pedido = self._pedido()
        self._item(pedido)

        pedido_http = RequestFactory().post(f'/moda/comercial/pedidos/{pedido.pk}/ordens/')
        pedido_http.filial_ativa = self.filial
        pedido_http.user = None
        pedido_http.session = {}
        pedido_http._messages = FallbackStorage(pedido_http)

        resposta = OrdemGerarView().post(pedido_http, pedido.pk)

        self.assertEqual(resposta.status_code, 302)
        self.assertIn(str(pedido.pk), resposta['Location'])
