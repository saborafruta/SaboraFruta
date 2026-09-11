from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.core.services.exceptions import DomainError
from apps.financeiro.constants.enums import StatusContaReceber, TipoFormaPagamento
from apps.financeiro.models import ContaBancaria, CondicaoPagamento, FormaPagamento
from apps.financeiro.models.receber_pagar import ContaReceber, PagamentoContaReceber
from apps.moda.models import ItemPedidoProducao, PedidoProducao, ProdutoModa
from apps.moda.services.financeiro import FinanceiroPedidoService


class RecebimentoOpPosicaoDiariaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Empresa Receber OP LTDA',
            nome_fantasia='Receber OP',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa,
            razao_social='Filial Receber OP',
            cnpj='53345678000192',
            uf='RN',
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Administrador', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='receber-op@inoovated.com',
            nome='Financeiro OP',
            password='teste1234',
            empresa=cls.empresa,
            filial=cls.filial,
            perfil=perfil,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial,
            razao_social='Cliente da OP',
            cpf_cnpj='12345678901',
            ativo=True,
        )
        cls.banco = ContaBancaria.objects.create(
            filial=cls.filial,
            descricao='Banco principal',
            banco_codigo='260',
            banco_nome='Nubank',
        )
        cls.forma = FormaPagamento.objects.create(
            empresa=cls.empresa,
            filial=cls.filial,
            descricao='PIX',
            tipo=TipoFormaPagamento.PIX,
            conta_bancaria_padrao=cls.banco,
        )
        cls.condicao = CondicaoPagamento.objects.create(
            empresa=cls.empresa,
            descricao='À vista',
            numero_parcelas=1,
            intervalo_dias=0,
            dias_primeira_parcela=0,
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session['filial_id'] = self.filial.pk
        session.save()

    def _pedido(self, *, numero=18, entrada=Decimal('0'), quantidade=10, unitario=Decimal('100')):
        produto = ProdutoModa.objects.create(
            filial=self.filial,
            codigo=f'OPR{numero:03d}',
            nome=f'Produto OP {numero}',
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial,
            cliente=self.cliente,
            numero=numero,
            data_pedido=date(2026, 9, 11),
            data_prevista_entrega=date(2026, 9, 20),
            entrada=entrada,
            condicao_pagamento=self.condicao,
        )
        ItemPedidoProducao.objects.create(
            pedido=pedido,
            produto=produto,
            descricao='Uniforme',
            quantidade=quantidade,
            valor_unitario=unitario,
        )
        return pedido

    def _dados_recebimento(self, pedido, valor):
        return {
            'acao': 'receber_op',
            'pedido_id': pedido.pk,
            'data_referencia': '2026-09-11',
            'data_pagamento': '2026-09-11',
            'valor_pago': str(valor),
            'valor_taxa': '0.00',
            'valor_liquido': str(valor),
            'valor_juros': '0.00',
            'valor_multa': '0.00',
            'valor_desconto': '0.00',
            'forma_pagamento': self.forma.pk,
            'conta_bancaria': self.banco.pk,
            'bandeira': '',
            'numero_parcelas': '',
            'observacao': 'Pagamento conferido pelo financeiro',
        }

    def test_lista_op_sem_gerar_financeiro_e_nao_trata_entrada_digitada_como_recebida(self):
        pedido = self._pedido(entrada=Decimal('200.00'))

        pendente = FinanceiroPedidoService.pedidos_com_saldo(self.filial)[0]

        self.assertEqual(pendente.pk, pedido.pk)
        self.assertEqual(pendente.valor_recebido_op, Decimal('0.00'))
        self.assertEqual(pendente.valor_aberto_op, Decimal('1000.00'))
        self.assertFalse(pendente.tem_financeiro_op)
        response = self.client.get(reverse('financeiro:posicao_diaria'), {'data': '2026-09-11'})
        self.assertContains(response, "+ Receber OP's")
        self.assertContains(response, 'Financeiro ainda não gerado')
        self.assertContains(response, 'OP #000018')
        self.assertContains(response, 'Ver OP')
        self.assertContains(response, reverse('moda:op2-detail', args=[pedido.pk]))
        self.assertContains(response, 'style="z-index:300;')

    def test_recebimento_parcial_cria_titulo_integral_vinculado_e_bloqueia_duplicacao(self):
        pedido = self._pedido()

        response = self.client.post(
            reverse('financeiro:posicao_diaria'),
            self._dados_recebimento(pedido, Decimal('300.00')),
        )

        self.assertRedirects(
            response,
            reverse('financeiro:posicao_diaria') + '?data=2026-09-11',
            fetch_redirect_response=False,
        )
        titulo = ContaReceber.objects.get(
            documento_tipo=FinanceiroPedidoService.DOCUMENTO_TIPO,
            documento_id=pedido.pk,
        )
        self.assertEqual(titulo.valor_original, Decimal('1000.00'))
        self.assertEqual(titulo.valor_pago, Decimal('300.00'))
        self.assertEqual(titulo.valor_saldo, Decimal('700.00'))
        self.assertEqual(titulo.status, StatusContaReceber.PAGO_PARCIAL)
        pedido.refresh_from_db()
        self.assertIsNotNone(pedido.financeiro_gerado_em)
        situacao = FinanceiroPedidoService.situacao_pagamento(
            titulo.valor_final, titulo.valor_pago, titulo.valor_saldo,
        )
        self.assertEqual(situacao['chave'], 'parcial')
        with self.assertRaisesMessage(DomainError, 'já tem financeiro gerado'):
            FinanceiroPedidoService.gerar(pedido)

    def test_segundo_recebimento_quita_mesmo_titulo_sem_duplicar(self):
        pedido = self._pedido()
        FinanceiroPedidoService.receber(
            pedido,
            data_pagamento=date(2026, 9, 11),
            valor_pago=Decimal('300.00'),
            forma_pagamento=self.forma,
            usuario=self.usuario,
            conta_bancaria=self.banco,
        )

        resultado = FinanceiroPedidoService.receber(
            pedido,
            data_pagamento=date(2026, 9, 12),
            valor_pago=Decimal('700.00'),
            forma_pagamento=self.forma,
            usuario=self.usuario,
            conta_bancaria=self.banco,
        )

        self.assertEqual(resultado.saldo_restante, Decimal('0.00'))
        self.assertEqual(ContaReceber.objects.filter(documento_id=pedido.pk).count(), 1)
        titulo = ContaReceber.objects.get(documento_id=pedido.pk)
        self.assertEqual(titulo.status, StatusContaReceber.PAGO)
        self.assertEqual(titulo.pagamentos.count(), 2)
        self.assertFalse(any(p.pk == pedido.pk for p in FinanceiroPedidoService.pedidos_com_saldo(self.filial)))

    def test_taxa_fixa_e_calculada_uma_vez_quando_recebimento_cruza_parcelas(self):
        pedido = self._pedido()
        self.condicao.numero_parcelas = 2
        self.condicao.intervalo_dias = 30
        self.condicao.save(update_fields=['numero_parcelas', 'intervalo_dias'])
        self.forma.taxa_administrativa = Decimal('1.00')
        self.forma.taxa_fixa = Decimal('2.00')
        self.forma.save(update_fields=['taxa_administrativa', 'taxa_fixa'])
        FinanceiroPedidoService.gerar(pedido, parcelas_saldo=2)

        resultado = FinanceiroPedidoService.receber(
            pedido,
            data_pagamento=date(2026, 9, 11),
            valor_pago=Decimal('600.00'),
            forma_pagamento=self.forma,
            usuario=self.usuario,
            conta_bancaria=self.banco,
        )

        pagamentos = PagamentoContaReceber.objects.filter(
            conta_receber__documento_id=pedido.pk,
        )
        self.assertEqual(resultado.pagamentos_criados, 2)
        self.assertEqual(resultado.valor_taxa, Decimal('8.00'))
        self.assertEqual(sum((p.valor_taxa for p in pagamentos), Decimal('0')), Decimal('8.00'))
        self.assertEqual(sum((p.valor_liquido for p in pagamentos), Decimal('0')), Decimal('592.00'))

    def test_valor_acima_do_saldo_nao_cria_titulo(self):
        pedido = self._pedido()

        response = self.client.post(
            reverse('financeiro:posicao_diaria'),
            self._dados_recebimento(pedido, Decimal('1000.01')),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'não pode passar do saldo da OP')
        self.assertFalse(ContaReceber.objects.filter(documento_id=pedido.pk).exists())
