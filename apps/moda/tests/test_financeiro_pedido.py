from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.core.services.exceptions import DomainError
from apps.financeiro.constants.enums import StatusContaReceber, TipoFormaPagamento
from apps.financeiro.models import ContaBancaria, CondicaoPagamento, FormaPagamento
from apps.financeiro.models.conta_bancaria import PlanoContas
from apps.financeiro.models.receber_pagar import ContaReceber, PagamentoContaReceber
from apps.moda.models import ItemPedidoProducao, PedidoProducao, ProdutoModa
from apps.moda.services.financeiro import FinanceiroPedidoService


class FinanceiroPedidoEntradaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social="Confeccao Financeiro LTDA",
            nome_fantasia="Financeiro Moda",
            cnpj="53345678000191",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social="Confeccao Financeiro LTDA",
            cnpj="53345678000272",
            uf="RN",
            cidade="Natal",
            is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial,
            razao_social="Cliente Moda",
            cpf_cnpj="12345678901",
            ativo=True,
        )
        cls.cliente_2 = Cliente.objects.create(
            filial=cls.filial,
            razao_social="Segundo Cliente Moda",
            cpf_cnpj="98765432100",
            ativo=True,
        )
        cls.conta = ContaBancaria.objects.create(
            filial=cls.filial,
            descricao="Conta PIX",
            banco_codigo="260",
            banco_nome="Nubank",
        )
        cls.forma = FormaPagamento.objects.create(
            empresa=cls.empresa,
            filial=cls.filial,
            descricao="PIX",
            tipo=TipoFormaPagamento.PIX,
            conta_bancaria_padrao=cls.conta,
        )
        cls.condicao = CondicaoPagamento.objects.create(
            empresa=cls.empresa,
            descricao="A vista",
            numero_parcelas=1,
            intervalo_dias=0,
            dias_primeira_parcela=0,
        )
        cls.categoria_vendas = PlanoContas.objects.create(
            empresa=cls.empresa,
            codigo='3100100001',
            descricao='Vendas de produtos',
            tipo='R',
            nivel=3,
            aceita_lancamento=True,
        )

    def _pedido(self, entrada=Decimal("0"), forma=None, conta=None, entrega=date(2026, 9, 5)):
        produto = ProdutoModa.objects.create(
            filial=self.filial,
            codigo=f"CAM{ProdutoModa.objects.count() + 1:03d}",
            nome="Camisa",
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial,
            cliente=self.cliente,
            numero=PedidoProducao.objects.count() + 1,
            data_pedido=date(2026, 8, 25),
            data_prevista_entrega=entrega,
            entrada=entrada,
            forma_pagamento=forma,
            conta_bancaria_entrada=conta,
            condicao_pagamento=self.condicao,
        )
        ItemPedidoProducao.objects.create(
            pedido=pedido,
            produto=produto,
            descricao="Camisa",
            quantidade=10,
            valor_unitario=Decimal("100.00"),
        )
        return pedido

    def test_entrada_do_pedido_vira_recebimento_baixado_e_saldo_fica_aberto(self):
        pedido = self._pedido(entrada=Decimal("200.00"), forma=self.forma)

        contas = FinanceiroPedidoService.gerar(pedido)

        self.assertEqual(len(contas), 2)
        entrada = ContaReceber.objects.get(parcela=1)
        saldo = ContaReceber.objects.get(parcela=2)
        self.assertEqual(entrada.status, StatusContaReceber.PAGO)
        self.assertEqual(entrada.valor_original, Decimal("200.00"))
        self.assertEqual(entrada.valor_pago, Decimal("200.00"))
        self.assertEqual(entrada.valor_saldo, Decimal("0.00"))
        self.assertEqual(entrada.conta_bancaria, self.conta)
        pagamento = PagamentoContaReceber.objects.get(conta_receber=entrada)
        self.assertEqual(pagamento.valor_pago, Decimal("200.00"))
        self.assertEqual(pagamento.forma_pagamento, self.forma)
        self.assertEqual(pagamento.conta_bancaria, self.conta)
        self.assertEqual(saldo.status, StatusContaReceber.ABERTO)
        self.assertEqual(saldo.valor_original, Decimal("800.00"))
        self.assertEqual(saldo.valor_pago, Decimal("0"))
        self.assertEqual(saldo.data_vencimento, date(2026, 9, 5))
        self.assertTrue(all(conta.plano_contas == self.categoria_vendas for conta in contas))

    def test_saldo_respeita_limite_de_trinta_dias_da_data_do_pedido(self):
        pedido = self._pedido(
            entrada=Decimal("200.00"),
            forma=self.forma,
            entrega=date(2026, 10, 20),
        )

        FinanceiroPedidoService.gerar(pedido)

        saldo = ContaReceber.objects.get(parcela=2)
        self.assertEqual(saldo.data_vencimento, date(2026, 9, 24))

    def test_sem_previsao_de_entrega_saldo_vence_no_limite_de_trinta_dias(self):
        pedido = self._pedido(
            entrada=Decimal("200.00"),
            forma=self.forma,
            entrega=None,
        )

        FinanceiroPedidoService.gerar(pedido)

        saldo = ContaReceber.objects.get(parcela=2)
        self.assertEqual(saldo.data_vencimento, date(2026, 9, 24))

    def test_sem_entrada_nao_exige_forma_e_nao_cria_pagamento(self):
        pedido = self._pedido(entrada=Decimal("0.00"))

        contas = FinanceiroPedidoService.gerar(pedido)

        self.assertEqual(len(contas), 1)
        self.assertEqual(contas[0].valor_original, Decimal("1000.00"))
        self.assertFalse(PagamentoContaReceber.objects.exists())
        self.assertEqual(contas[0].status_entrega, ContaReceber.StatusEntrega.PREVISTA)
        self.assertEqual(contas[0].data_entrega_prevista, date(2026, 9, 5))

    def test_alterar_previsao_da_op_sincroniza_titulos_gerados(self):
        pedido = self._pedido(entrada=Decimal("0.00"))
        FinanceiroPedidoService.gerar(pedido)
        pedido.data_prevista_entrega = date(2026, 9, 20)
        pedido.save(update_fields=['data_prevista_entrega'])

        FinanceiroPedidoService.sincronizar_previsao_entrega(pedido)

        conta = ContaReceber.objects.get()
        self.assertEqual(conta.data_entrega_prevista, date(2026, 9, 20))
        self.assertEqual(conta.status_entrega, ContaReceber.StatusEntrega.PREVISTA)

    def test_entrada_exige_forma_e_conta_bancaria(self):
        pedido = self._pedido(entrada=Decimal("50.00"))
        with self.assertRaisesMessage(DomainError, "forma de pagamento da entrada"):
            FinanceiroPedidoService.gerar(pedido)

        self.forma.conta_bancaria_padrao = None
        self.forma.save(update_fields=["conta_bancaria_padrao"])
        pedido.forma_pagamento = self.forma
        with self.assertRaisesMessage(DomainError, "conta bancária da entrada"):
            FinanceiroPedidoService.gerar(pedido)

    def test_saldo_usa_vencimento_e_parcelas_confirmados_no_modal(self):
        pedido = self._pedido(entrada=Decimal("500.00"), forma=self.forma)

        contas = FinanceiroPedidoService.gerar(
            pedido, vencimento_saldo=date(2026, 10, 10), parcelas_saldo=2,
        )

        self.assertEqual(len(contas), 3)
        saldos = list(ContaReceber.objects.filter(
            status=StatusContaReceber.ABERTO,
        ).order_by('parcela'))
        self.assertEqual([c.valor_original for c in saldos], [
            Decimal('250.00'), Decimal('250.00'),
        ])
        self.assertEqual([c.data_vencimento for c in saldos], [
            date(2026, 10, 10), date(2026, 11, 9),
        ])
        self.assertTrue(all(c.conta_bancaria == self.conta for c in saldos))

    def test_pagamento_e_fiado_podem_ser_divididos_entre_clientes(self):
        pedido = self._pedido(entrada=Decimal("200.01"), forma=self.forma)
        pedido.clientes_adicionais.add(self.cliente_2)

        contas = FinanceiroPedidoService.gerar(
            pedido,
            pagadores_entrada=[self.cliente, self.cliente_2],
            devedores_saldo=[self.cliente, self.cliente_2],
        )

        self.assertEqual(len(contas), 4)
        self.assertEqual(
            sum((conta.valor_original for conta in contas), Decimal('0')),
            Decimal('1000.00'),
        )
        pagas = [conta for conta in contas if conta.status == StatusContaReceber.PAGO]
        abertas = [conta for conta in contas if conta.status == StatusContaReceber.ABERTO]
        self.assertEqual({conta.cliente_id for conta in pagas}, {self.cliente.pk, self.cliente_2.pk})
        self.assertEqual({conta.cliente_id for conta in abertas}, {self.cliente.pk, self.cliente_2.pk})
        self.assertEqual(
            sum((conta.valor_pago for conta in pagas), Decimal('0')),
            Decimal('200.01'),
        )
        self.assertEqual(
            sum((conta.valor_saldo for conta in abertas), Decimal('0')),
            Decimal('799.99'),
        )
